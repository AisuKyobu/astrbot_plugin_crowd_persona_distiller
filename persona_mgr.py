import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .storage import GroupFriendStorage


def slugify(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        parts = lazy_pinyin(name)
        return "_".join(parts)
    except ImportError:
        result = []
        for ch in name.lower():
            if ch.isascii() and (ch.isalnum() or ch in ("-", "_")):
                result.append(ch)
            elif ch == " ":
                result.append("_")
        slug = "".join(result)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug if slug else "pig"


class PersonaManager:
    def __init__(self, context: Context, storage: GroupFriendStorage, config: dict):
        self.context = context
        self.storage = storage
        self.config = config

        data_dir = (
            Path(get_astrbot_data_path())
            / "plugin_data"
            / "astrbot_plugin_crowd_persona_distiller"
        )
        self.pigs_dir = data_dir / "pigs"
        self.pigs_dir.mkdir(parents=True, exist_ok=True)

    def _persona_dir(self, slug: str) -> Path:
        return self.pigs_dir / slug

    def _load_prompts(self):
        plugin_dir = Path(__file__).parent
        analyzer = (plugin_dir / "prompts" / "persona_analyzer.md").read_text(
            encoding="utf-8"
        )
        builder = (plugin_dir / "prompts" / "persona_builder.md").read_text(
            encoding="utf-8"
        )
        return analyzer, builder

    # ---------- CRUD ----------

    def list_all(self) -> list[dict]:
        result = []
        for d in sorted(self.pigs_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            result.append(meta)
        return result

    def load_persona(self, slug: str) -> Optional[str]:
        persona_path = self._persona_dir(slug) / "persona.md"
        if persona_path.exists():
            return persona_path.read_text(encoding="utf-8")
        return None

    def load_meta(self, slug: str) -> Optional[dict]:
        meta_path = self._persona_dir(slug) / "meta.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
        return None

    def save_persona(self, slug: str, content: str):
        d = self._persona_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "versions").mkdir(exist_ok=True)
        (d / "persona.md").write_text(content, encoding="utf-8")

    def save_meta(self, slug: str, meta: dict):
        d = self._persona_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def delete_persona(self, slug: str):
        import shutil

        d = self._persona_dir(slug)
        if d.exists():
            shutil.rmtree(d)

    # ---------- 蒸馏 ----------

    async def distill(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        manual_tags: dict | None = None,
    ) -> Optional[str]:
        """执行蒸馏。前置校验（provider/消息数）由调用方负责。"""
        provider_id = self.config.get("distill_provider", "")
        if not provider_id:
            return None

        count = await self.storage.get_user_all_message_count(group_id, user_id)
        messages = await self.storage.get_user_all_messages(group_id, user_id, limit=500)
        if not messages:
            return None

        analyzer_prompt, builder_prompt = self._load_prompts()

        formatted = self._format_messages_for_llm(messages, user_name)
        tags_text = self._format_tags(manual_tags or {})

        user_prompt = (
            f"## 手动标签\n{tags_text}\n\n"
            f"## 聊天记录（共 {len(messages)} 条）\n{formatted}"
        )

        slug = slugify(f"{user_name}_{group_id}") if group_id else slugify(user_name)

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                system_prompt=analyzer_prompt,
            )
            analysis_text = llm_resp.completion_text if llm_resp else ""
        except Exception as e:
            logger.error(f"[群友蒸馏] LLM 分析失败: {e}")
            return None

        persona_content = await self._build_persona(
            analysis_text, builder_prompt, provider_id, manual_tags
        )

        if not persona_content:
            return None

        self.save_persona(slug, persona_content)

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "name": user_name,
            "slug": slug,
            "group_id": group_id,
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
            "last_distill_at": now,
            "version": "v1",
            "message_count": count,
            "profile": manual_tags.get("profile", {}) if manual_tags else {},
            "tags": manual_tags.get("tags", {}) if manual_tags else {},
        }
        self.save_meta(slug, meta)

        await self.storage.create_persona_index(
            slug, user_name, group_id, user_id, count
        )
        await self.storage.update_persona_index(slug, count, now)

        logger.info(f"[群友蒸馏] 蒸馏完成: {user_name} ({slug}), {count} 条消息")
        return slug

    async def incremental_update(
        self, slug: str, group_id: str, user_id: str
    ) -> Optional[dict]:
        """增量更新：用新消息追加到现有 persona.md"""
        provider_id = self.config.get("distill_provider", "")
        if not provider_id:
            return None

        existing = self.load_persona(slug)
        if not existing:
            return None

        meta = self.load_meta(slug) or {}
        last_at = meta.get("last_distill_at", "")
        since = 0
        if last_at:
            try:
                since = datetime.fromisoformat(last_at).timestamp()
            except Exception:
                pass

        new_messages = await self.storage.get_user_all_messages_since(
            group_id, user_id, since, limit=300
        )
        if not new_messages:
            return {"status": "no_new_messages", "message": "没有新消息，无需更新"}

        merger_prompt = (
            Path(__file__).parent / "prompts" / "merger.md"
        ).read_text(encoding="utf-8")

        formatted = self._format_messages_for_llm(
            new_messages, meta.get("name", "")
        )

        build_prompt = (
            f"{merger_prompt}\n\n"
            f"## 现有 persona.md\n```markdown\n{existing}\n```\n\n"
            f"## 新聊天记录\n{formatted}\n\n"
            f"请判断新内容应该更新 persona.md 的哪个部分，输出增量更新。"
        )

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=build_prompt,
                system_prompt="",
            )
            patch = llm_resp.completion_text.strip() if llm_resp else ""
        except Exception as e:
            logger.error(f"[群友蒸馏] 增量更新 LLM 调用失败: {e}")
            return None

        if not patch:
            return None

        # 追加 patch 到现有 persona.md
        now = datetime.now(timezone.utc).isoformat()
        version = meta.get("version", "v1")
        next_version = f"v{int(version.lstrip('v')) + 1}"

        new_content = (
            f"{existing.rstrip()}\n\n"
            f"## 增量更新记录（{now}）\n\n"
            f"{patch}\n"
        )
        self.save_persona(slug, new_content)

        meta["version"] = next_version
        meta["updated_at"] = now
        meta["last_distill_at"] = now
        self.save_meta(slug, meta)

        await self.storage.update_persona_index(
            slug, message_count=meta.get("message_count", 0), last_distill_at=now
        )

        logger.info(f"[群友蒸馏] 增量更新完成: {slug} ({version} → {next_version})")
        return {
            "status": "ok",
            "slug": slug,
            "version": next_version,
            "patch_preview": patch[:200],
        }

    async def correct_persona(
        self, slug: str, correction_text: str
    ) -> Optional[dict]:
        """根据用户修正意见，调用 LLM 更新 persona.md。返回 {summary, content, status}"""
        import json, re

        provider_id = self.config.get("distill_provider", "")
        if not provider_id:
            return None

        existing = self.load_persona(slug)
        if not existing:
            return None

        correction_prompt = (
            Path(__file__).parent / "prompts" / "correction_handler.md"
        ).read_text(encoding="utf-8")

        build_prompt = (
            f"{correction_prompt}\n\n"
            f"## 当前 persona.md\n```markdown\n{existing}\n```\n\n"
            f"## 用户修正意见\n{correction_text}"
        )

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=build_prompt,
            )
            response = llm_resp.completion_text.strip() if llm_resp else ""
        except Exception as e:
            logger.error(f"[群友蒸馏] 人格修正 LLM 调用失败: {e}")
            return None

        if not response:
            return None

        # 提取 JSON（兼容 ```json 包裹和纯 JSON）
        match = re.search(r'\{[\s\S]*\}', response)
        if not match:
            logger.error(f"[群友蒸馏] 人格修正 LLM 未返回 JSON")
            return None

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.error(f"[群友蒸馏] 人格修正 JSON 解析失败: {e}")
            return None

        changes = data.get("changes", []) if isinstance(data, dict) else []
        persona = data.get("persona", "") if isinstance(data, dict) else ""

        if not persona:
            return None

        # 检测是否真的产生了修改
        normalized_existing = existing.rstrip()
        normalized_persona = persona.rstrip()

        if not changes or normalized_persona == normalized_existing:
            logger.info(f"[群友蒸馏] 人格修正跳过：LLM 未作出修改 ({slug})")
            return {"status": "no_changes", "summary": "", "content": persona}

        self.save_persona(slug, persona)

        now = datetime.now(timezone.utc).isoformat()
        meta = self.load_meta(slug) or {}
        meta["updated_at"] = now
        self.save_meta(slug, meta)

        await self.storage.update_persona_index(
            slug, message_count=meta.get("message_count", 0), last_distill_at=now
        )

        summary = "\n".join(f"- {c}" for c in changes if isinstance(c, str))
        logger.info(f"[群友蒸馏] 人格修正完成: {slug}")
        return {"summary": summary, "content": persona, "status": "ok"}

    async def _build_persona(
        self,
        analysis_text: str,
        builder_prompt: str,
        provider_id: str,
        manual_tags: dict | None,
    ) -> Optional[str]:
        build_prompt = (
            f"{builder_prompt}\n\n"
            f"## 分析结果\n{analysis_text}\n\n"
            f"## 用户标签\n{self._format_tags(manual_tags or {})}\n\n"
            f"请根据以上信息生成 persona.md 完整内容。直接输出 markdown，不要包含代码块标记。"
        )
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=build_prompt,
            )
            return llm_resp.completion_text.strip() if llm_resp else None
        except Exception as e:
            logger.error(f"[群友蒸馏] Persona 生成失败: {e}")
            return analysis_text

    # ---------- 导入 ----------

    async def import_from_messages(
        self,
        group_id: str,
        user_id: str,
        user_name: str,
        messages: list[dict],
        chat_type: str = "group",
    ) -> int:
        count = 0
        for msg in messages:
            content = msg.get("content", "").strip()
            if not content:
                continue
            ts = msg.get("timestamp", None)
            if isinstance(ts, (int, float)):
                ts = int(ts)
            else:
                ts = None
            await self.storage.record_message(group_id, user_id, user_name, content, ts, chat_type=chat_type)
            count += 1
        logger.info(f"[群友蒸馏] 导入 {user_name} 的 {count} 条消息 (chat_type={chat_type})")
        return count

    # ---------- 辅助 ----------

    def _format_messages_for_llm(self, messages: list[dict], user_name: str) -> str:
        long_msgs, interactive, daily = [], [], []
        for m in messages:
            content = m["content"]
            if len(content) > 50:
                long_msgs.append(m)
            elif any(kw in content for kw in ["@", "回复", "引用"]):
                interactive.append(m)
            else:
                daily.append(m)

        uid = messages[0]["user_id"] if messages else ""
        display_name = self._resolve_name(uid, user_name)
        aliases = self._get_aliases(uid)
        if len(aliases) > 1:
            display_name += f"（别名: {', '.join(aliases[1:])}）"

        lines = [
            f"## {display_name} 的聊天记录分类",
            "",
            f"### 长消息（{len(long_msgs)} 条，权重最高）",
        ]
        for m in long_msgs:
            lines.append(f"- {m['content']}")
        lines.append("")
        lines.append(f"### 互动类消息（{len(interactive)} 条）")
        for m in interactive:
            lines.append(f"- {m['content']}")
        lines.append("")
        lines.append(f"### 日常消息（{len(daily)} 条，仅展示前 100）")
        for m in daily[:100]:
            lines.append(f"- {m['content']}")
        return "\n".join(lines)

    def _format_tags(self, tags: dict) -> str:
        if not tags:
            return "（无手动标签）"
        lines = []
        profile = tags.get("profile", {})
        if profile:
            parts = []
            if profile.get("age"):
                parts.append(f"{profile['age']}岁")
            if profile.get("gender"):
                parts.append(profile["gender"])
            if profile.get("occupation"):
                parts.append(profile["occupation"])
            if parts:
                lines.append(f"基本信息：{' '.join(parts)}")
            if profile.get("hobbies"):
                lines.append(f"兴趣：{profile['hobbies']}")
            if profile.get("mbti"):
                lines.append(f"MBTI：{profile['mbti']}")
        tag_list = tags.get("tags", {})
        if tag_list:
            for k, v in tag_list.items():
                if isinstance(v, list):
                    lines.append(f"{k}：{', '.join(v)}")
                else:
                    lines.append(f"{k}：{v}")
        impression = tags.get("impression", "")
        if impression:
            lines.append(f"印象：{impression}")
        return "\n".join(lines) if lines else "（无手动标签）"

    def _resolve_name(self, user_id: str, fallback: str) -> str:
        for item in self.config.get("nickname_mappings", []):
            if isinstance(item, str):
                uid, sep, rest = item.partition(",")
                if uid.strip() == user_id and rest.strip():
                    return rest.split(",")[0].strip()
        return fallback

    def _get_aliases(self, user_id: str) -> list[str]:
        for item in self.config.get("nickname_mappings", []):
            if isinstance(item, str):
                uid, sep, rest = item.partition(",")
                if uid.strip() == user_id and rest.strip():
                    return [a.strip() for a in rest.split(",") if a.strip()]
        return []

    async def get_random_persona_for_group(self, group_id: str) -> Optional[dict]:
        personas = await self.storage.get_personas_by_group(group_id)
        if not personas:
            return None
        import random

        return random.choice(personas)
