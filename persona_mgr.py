"""PersonaManager — 群友人格蒸馏管理(Phase 3 重写版 + Phase 5 别名标注)

核心改动:
- 新增 _compute_stats:在调 LLM 前用 Python 算好词频/句长/活跃时段等硬指标
- _format_messages_for_llm 重写:按时间排序保留全部消息(带发言人/时间戳),并对**所有用户的别名**做 `alias(= 全名)` 标注
- distill() 改成两步:analyzer 输出严格 JSON → builder 基于 JSON + stats + 原文生成 persona.md
- meta 写 main_nick / aliases / stats,供 WebUI modal 展示
- incremental_update() 改用 merger.md 走完整覆盖而非 append
"""
import json
import re
from collections import Counter
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


_EMOJI_RANGES = [
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F700, 0x1F77F),
    (0x1F900, 0x1F9FF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
]


def _is_emoji(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def _extract_emojis(text: str) -> list[str]:
    return [ch for ch in text if _is_emoji(ch)]


_CJK_STOPWORDS = set(
    "的了在是我有和就不人都一一个也这那到说我们要你会着去看好"
    "自己但什么没啊呢吗吧还哦嗯哈呀嘛哎唉啦哦嘛呀他她它们"
)


def _tokenize_zh(text: str) -> list[str]:
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\S+", " ", text)
    text = re.sub(r"\d+", " ", text)
    text = re.sub(r"\[.*?\]", " ", text)
    tokens = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            tokens.append(ch)
        elif ch.isascii() and ch.isalpha():
            tokens.append(ch.lower())
    out = []
    i = 0
    while i < len(tokens):
        if "\u4e00" <= tokens[i] <= "\u9fff" and tokens[i] in _CJK_STOPWORDS:
            i += 1
            continue
        out.append(tokens[i])
        i += 1
    grams = []
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
            grams.append(a + b)
    return out + grams


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

    def _load_prompt(self, name: str) -> str:
        return (Path(__file__).parent / "prompts" / name).read_text(encoding="utf-8")

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
        provider_id = self.config.get("distill_provider", "")
        if not provider_id:
            return None

        count = await self.storage.get_user_all_message_count(group_id, user_id)
        msg_limit = int(self.config.get("distill_message_limit", 500))
        messages = await self.storage.get_user_all_messages(group_id, user_id, limit=msg_limit)
        if not messages:
            return None

        stats_text = self._compute_stats(messages, user_name)
        # 解析主名(从 nickname_mappings 拿,避免 user_name 是简称)
        main_nick = self._resolve_name(user_id, user_name)
        aliases = self._get_aliases(user_id)
        messages_text = self._format_messages_for_llm(messages, user_name)
        samples = self._sample_messages(messages, n=10)

        analyzer_prompt = self._load_prompt("persona_analyzer.md")
        user_prompt = (
            f"## 用户手动标签\n{self._format_tags(manual_tags or {})}\n\n"
            f"## 硬指标块\n{stats_text}\n\n"
            f"## 聊天原文(按时间排序,带发言人 + 时间戳)\n{messages_text}\n\n"
            f"请按 schema 输出严格 JSON,不要任何其他文字。"
        )

        # 蒸馏时可选带上图片(默认关闭,会增加 token 消耗)
        distill_with_images = bool(self.config.get("distill_with_images", False))
        analyzer_image_urls: list[str] = []
        if distill_with_images:
            analyzer_image_urls = self._collect_image_urls(messages, max_n=10)

        slug = slugify(f"{main_nick}_{group_id}") if group_id else slugify(main_nick)

        try:
            llm_kwargs = {
                "chat_provider_id": provider_id,
                "prompt": user_prompt,
                "system_prompt": analyzer_prompt,
            }
            if analyzer_image_urls:
                llm_kwargs["image_urls"] = analyzer_image_urls
            llm_resp = await self.context.llm_generate(**llm_kwargs)
            analysis_text = (llm_resp.completion_text if llm_resp else "") or ""
        except Exception as e:
            logger.error(f"[群友蒸馏] LLM 分析失败: {e}")
            return None

        analysis_json = self._extract_json(analysis_text)
        if not analysis_json:
            logger.warning("[群友蒸馏] analyzer 未返回有效 JSON,回退用原文做 evidence")
            analysis_json = {"raw_analysis": analysis_text, "core_personality": []}

        builder_prompt = self._load_prompt("persona_builder.md")
        persona_content = await self._build_persona(
            analysis_json, stats_text, samples, builder_prompt, provider_id,
            main_nick, count, manual_tags, aliases,
        )
        if not persona_content:
            return None

        self.save_persona(slug, persona_content)

        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "name": main_nick,
            "slug": slug,
            "group_id": group_id,
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
            "last_distill_at": now,
            "version": "v2",
            "message_count": count,
            "profile": (manual_tags or {}).get("profile", {}),
            "tags": (manual_tags or {}).get("tags", {}),
            "schema": "v2-stats-json",
            "main_nick": main_nick,
            "aliases": aliases,
            "stats": stats_text,
        }
        self.save_meta(slug, meta)

        await self.storage.create_persona_index(
            slug, main_nick, group_id, user_id, count
        )
        await self.storage.update_persona_index(slug, count, now)

        logger.info(f"[群友蒸馏] 蒸馏完成: {main_nick} ({slug}), {count} 条消息")
        return slug

    async def incremental_update(
        self, slug: str, group_id: str, user_id: str
    ) -> Optional[dict]:
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

        new_stats = self._compute_stats(new_messages, meta.get("name", ""))
        new_text = self._format_messages_for_llm(new_messages, meta.get("name", ""))
        samples = self._sample_messages(new_messages, n=8)

        analyzer_prompt = self._load_prompt("persona_analyzer.md")
        try:
            llm_an = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=(
                    f"## 硬指标(新消息)\n{new_stats}\n\n"
                    f"## 聊天原文(新消息)\n{new_text}\n\n"
                    f"请按 schema 输出严格 JSON。"
                ),
                system_prompt=analyzer_prompt,
            )
            analysis_text = (llm_an.completion_text if llm_an else "") or ""
        except Exception as e:
            logger.error(f"[群友蒸馏] 增量 analyzer 失败: {e}")
            return None
        analysis_json = self._extract_json(analysis_text) or {"raw_analysis": analysis_text}

        merger_prompt = self._load_prompt("merger.md")
        try:
            llm_merger = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=(
                    f"## 现有 persona.md\n```markdown\n{existing}\n```\n\n"
                    f"## 新消息的 analyzer JSON\n```json\n{json.dumps(analysis_json, ensure_ascii=False, indent=2)}\n```\n\n"
                    f"## 新消息的硬指标\n{new_stats}\n\n"
                    f"## 新消息的原文语料\n{self._format_samples(samples)}\n\n"
                    f"请输出新的完整 persona.md(覆盖式,不是 patch)。"
                ),
                system_prompt=merger_prompt,
            )
            new_content = (llm_merger.completion_text if llm_merger else "") or ""
        except Exception as e:
            logger.error(f"[群友蒸馏] 增量 merger 失败: {e}")
            return None

        new_content = self._strip_code_fence(new_content)
        if not new_content.strip():
            return None

        now = datetime.now(timezone.utc).isoformat()
        version = meta.get("version", "v2")
        try:
            v_num = int(str(version).lstrip("v")) + 1
        except Exception:
            v_num = 2
        next_version = f"v{v_num}"

        self.save_persona(slug, new_content)
        meta["version"] = next_version
        meta["updated_at"] = now
        meta["last_distill_at"] = now
        self.save_meta(slug, meta)

        await self.storage.update_persona_index(
            slug, message_count=meta.get("message_count", 0), last_distill_at=now
        )

        logger.info(f"[群友蒸馏] 增量更新完成: {slug} ({version} -> {next_version})")
        return {
            "status": "ok",
            "slug": slug,
            "version": next_version,
            "patch_preview": new_content[:200],
        }

    async def correct_persona(
        self, slug: str, correction_text: str
    ) -> Optional[dict]:
        provider_id = self.config.get("distill_provider", "")
        if not provider_id:
            return None

        existing = self.load_persona(slug)
        if not existing:
            return None

        correction_prompt = self._load_prompt("correction_handler.md")

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=(
                    f"## 当前 persona.md\n```markdown\n{existing}\n```\n\n"
                    f"## 用户修正意见\n{correction_text}"
                ),
            )
            response = (llm_resp.completion_text if llm_resp else "") or ""
        except Exception as e:
            logger.error(f"[群友蒸馏] 人格修正 LLM 调用失败: {e}")
            return None

        if not response:
            return None

        data = self._extract_json(response)
        if not data or not isinstance(data, dict):
            logger.error("[群友蒸馏] 人格修正 LLM 未返回 JSON")
            return None

        changes = data.get("changes", []) or []
        persona = data.get("persona", "") or ""
        persona = self._strip_code_fence(persona)

        if not persona:
            return None

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
        analysis_json: dict,
        stats_text: str,
        samples: list[dict],
        builder_prompt: str,
        provider_id: str,
        user_name: str,
        msg_count: int,
        manual_tags: dict | None,
        aliases: list[str] | None = None,
    ) -> Optional[str]:
        aliases_csv = ", ".join(aliases) if aliases else "(无)"
        build_prompt = (
            f"{builder_prompt}\n\n"
            f"---\n\n"
            f"## 硬指标块\n{stats_text}\n\n"
            f"## analyzer 分析结果(JSON)\n```json\n{json.dumps(analysis_json, ensure_ascii=False, indent=2)}\n```\n\n"
            f"## 用户手动标签\n{self._format_tags(manual_tags or {})}\n\n"
            f"## 聊天原文语料(由 builder 抽取 5-10 段使用)\n{self._format_samples(samples)}\n\n"
            f"---\n\n"
            f"## 模板变量\n"
            f"- name: {user_name}\n"
            f"- aliases_csv: {aliases_csv}\n\n"
            f"请生成 persona.md 完整内容。直接输出 markdown,不要包含代码块标记。"
        )
        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=build_prompt,
            )
            text = (llm_resp.completion_text if llm_resp else "") or ""
            text = self._strip_code_fence(text)
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"[群友蒸馏] Persona 生成失败: {e}")
            return None

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
            image_urls = msg.get("image_urls") or []
            await self.storage.record_message(
                group_id, user_id, user_name, content, ts,
                chat_type=chat_type, image_urls=image_urls,
            )
            count += 1
        logger.info(f"[群友蒸馏] 导入 {user_name} 的 {count} 条消息 (chat_type={chat_type})")
        return count

    # ---------- 硬指标计算 ----------

    def _compute_stats(self, messages: list[dict], user_name: str) -> str:
        if not messages:
            return "(无消息)"

        total = len(messages)
        lengths = [len(m.get("content", "")) for m in messages]
        avg_len = sum(lengths) / total if total else 0
        sorted_lens = sorted(lengths)
        median_len = sorted_lens[total // 2] if total else 0
        p90_idx = min(int(total * 0.9), total - 1) if total else 0
        p90_len = sorted_lens[p90_idx] if total else 0

        short_cnt = sum(1 for l in lengths if l <= 5)
        long_cnt = sum(1 for l in lengths if l >= 30)
        short_rate = short_cnt / total * 100 if total else 0
        long_rate = long_cnt / total * 100 if total else 0

        timestamps = [m.get("timestamp", 0) for m in messages if m.get("timestamp")]
        if timestamps:
            span_seconds = max(timestamps) - min(timestamps)
            span_days = max(1, span_seconds / 86400)
            msgs_per_day = total / span_days
            hours = [datetime.fromtimestamp(ts).hour for ts in timestamps if ts > 0]
            hour_hist = Counter(hours)
            top_hours = [h for h, _ in hour_hist.most_common(3)]
            tod = {"早(6-12)": 0, "中(12-18)": 0, "晚(18-24)": 0, "深夜(0-6)": 0}
            for h in hours:
                if 6 <= h < 12:
                    tod["早(6-12)"] += 1
                elif 12 <= h < 18:
                    tod["中(12-18)"] += 1
                elif 18 <= h < 24:
                    tod["晚(18-24)"] += 1
                else:
                    tod["深夜(0-6)"] += 1
            tod_pct = {k: f"{v / total * 100:.0f}%" for k, v in tod.items() if v > 0}
        else:
            msgs_per_day = 0
            top_hours = []
            tod_pct = {}

        all_text = " ".join(m.get("content", "") for m in messages)
        tokens = _tokenize_zh(all_text)
        word_counter = Counter(tokens)
        top_words = word_counter.most_common(20)

        all_emojis = []
        for m in messages:
            all_emojis.extend(_extract_emojis(m.get("content", "")))
        emoji_counter = Counter(all_emojis)
        top_emojis = emoji_counter.most_common(10)

        at_counter = Counter()
        for m in messages:
            content = m.get("content", "")
            for match in re.finditer(r"@([^\s@,，:：()（）]+)(?:[(（](\d+)[)）])?", content):
                at_counter[match.group(1)] += 1

        gaps = []
        if len(timestamps) >= 2:
            sorted_ts = sorted(timestamps)
            for i in range(1, len(sorted_ts)):
                gap = sorted_ts[i] - sorted_ts[i - 1]
                if 0 < gap < 3600 * 6:
                    gaps.append(gap)
            typical_gap = sum(gaps) / len(gaps) if gaps else 0
        else:
            typical_gap = 0

        lines = [f"## 硬指标(基于 {total} 条消息)"]
        lines.append(f"- 平均消息长度: {avg_len:.1f} 字")
        lines.append(f"- 中位消息长度: {median_len} 字")
        lines.append(f"- 90% 分位长度: {p90_len} 字")
        lines.append(f"- 短消息(≤5字)占 {short_rate:.0f}%,长消息(≥30字)占 {long_rate:.0f}%")
        lines.append(f"- 日均消息: {msgs_per_day:.1f} 条")
        if top_hours:
            lines.append(f"- 活跃时段 top3: {', '.join(f'{h}:00' for h in top_hours)}")
        if tod_pct:
            lines.append(f"- 时段分布: {', '.join(f'{k} {v}' for k, v in tod_pct.items())}")
        if top_words:
            lines.append(f"- 词频 top20: {', '.join(f'{w}({c})' for w, c in top_words)}")
        if top_emojis:
            lines.append(f"- emoji top10: {', '.join(f'{e}({c})' for e, c in top_emojis)}")
        if at_counter:
            at_top = at_counter.most_common(5)
            lines.append(f"- @ 习惯 top5: {', '.join(f'@{n}({c})' for n, c in at_top)}")
        if typical_gap:
            lines.append(f"- 消息间平均间隔: {typical_gap:.0f} 秒")
        return "\n".join(lines)

    def _format_messages_for_llm(self, messages: list[dict], user_name: str) -> str:
        """所有用户的消息按时间排序输出,带发言人 + 时间戳。
        对所有已知用户的别名,文本里都标 `alias(= 全名)`,帮 LLM 区分。
        """
        msgs = sorted(messages, key=lambda m: m.get("timestamp", 0))

        uid = msgs[0]["user_id"] if msgs else ""
        main_nick = self._resolve_name(uid, user_name)
        aliases = self._get_aliases(uid)
        alias_list = aliases[1:] if len(aliases) > 1 else []
        if alias_list:
            display_name = f"{main_nick}（全名）; 别名: {', '.join(alias_list)}"
        else:
            display_name = f"{main_nick}（全名）"

        if len(msgs) > 500:
            msgs = msgs[-500:]
            prefix = f"## {display_name} 的聊天记录(共 {len(msgs)} 条,已截取最近 500 条;他人用别名喊他,他自己说话时只用全名)\n\n"
        else:
            prefix = f"## {display_name} 的聊天记录(共 {len(msgs)} 条;他人用别名喊他,他自己说话时只用全名)\n\n"

        nicknames = self._parse_nicknames()
        uid_to_main = {u: lst[0] for u, lst in nicknames.items()}

        # 收集所有 alias -> main
        global_sub: dict[str, str] = {}
        for u, lst in nicknames.items():
            if len(lst) < 2:
                continue
            main = lst[0]
            for a in lst[1:]:
                if a and a != main:
                    global_sub.setdefault(a, main)

        def annotate_aliases(content: str) -> str:
            if not global_sub:
                return content
            aliases = sorted(global_sub.keys(), key=len, reverse=True)
            out = content
            for alias in aliases:
                if not alias:
                    continue
                main = global_sub[alias]
                token = f"{alias}(= {main})"
                out = out.replace(alias, token)
            return out

        lines = [prefix]
        for m in msgs:
            ts = m.get("timestamp", 0)
            ts_str = ""
            if ts and ts > 0:
                try:
                    dt = datetime.fromtimestamp(ts)
                    ts_str = dt.strftime("[%Y-%m-%d %H:%M:%S] ")
                except Exception:
                    pass
            other_uid = str(m.get("user_id", ""))
            other_name = uid_to_main.get(other_uid) or m.get("user_name", other_uid)
            content = m.get("content", "").replace("\n", " ")
            content = annotate_aliases(content)
            lines.append(f"{ts_str}{other_name}({other_uid}): {content}")
        return "\n".join(lines)

    def _sample_messages(self, messages: list[dict], n: int = 10) -> list[dict]:
        if not messages:
            return []
        if len(messages) <= n:
            return list(messages)
        msgs = sorted(messages, key=lambda m: m.get("timestamp", 0))
        n_each = max(1, n // 3)
        head = msgs[:n_each]
        mid_start = max(0, len(msgs) // 2 - n_each // 2)
        mid = msgs[mid_start : mid_start + n_each][:n_each]
        tail = msgs[-n_each:]
        sampled = head + mid + tail
        seen = set()
        out = []
        for m in sampled:
            mid_id = id(m)
            if mid_id in seen:
                continue
            seen.add(mid_id)
            out.append(m)
        if len(out) < n:
            for m in msgs:
                if id(m) not in seen:
                    out.append(m)
                    seen.add(id(m))
                    if len(out) >= n:
                        break
        return out[:n]

    @staticmethod
    def _collect_image_urls(messages: list[dict], max_n: int = 10) -> list[str]:
        """从 messages 里抽出所有 image URL,按出现顺序去重,最多 max_n 个。"""
        import json as _json
        seen: set[str] = set()
        out: list[str] = []
        for m in messages:
            raw = m.get("image_urls") or "[]"
            try:
                urls = _json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                urls = []
            for u in urls:
                if not isinstance(u, str) or not u or u.startswith("base64://"):
                    continue
                if u in seen:
                    continue
                seen.add(u)
                out.append(u)
                if len(out) >= max_n:
                    return out
        return out

    def _format_samples(self, samples: list[dict]) -> str:
        if not samples:
            return "(无样本)"
        lines = []
        nicknames = self._parse_nicknames()
        uid_to_main = {u: lst[0] for u, lst in nicknames.items()}
        for i, m in enumerate(samples, 1):
            ts = m.get("timestamp", 0)
            ts_str = ""
            if ts and ts > 0:
                try:
                    dt = datetime.fromtimestamp(ts)
                    ts_str = dt.strftime("[%Y-%m-%d %H:%M] ")
                except Exception:
                    pass
            other_uid = str(m.get("user_id", ""))
            other_name = uid_to_main.get(other_uid) or m.get("user_name", other_uid)
            content = m.get("content", "").replace("\n", " ")
            lines.append(f"  [{i}] {ts_str}{other_name}({other_uid}): {content}")
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
                lines.append(f"基本信息: {' '.join(parts)}")
            if profile.get("hobbies"):
                lines.append(f"兴趣: {profile['hobbies']}")
            if profile.get("mbti"):
                lines.append(f"MBTI: {profile['mbti']}")
        tag_list = tags.get("tags", {})
        if tag_list:
            for k, v in tag_list.items():
                if isinstance(v, list):
                    lines.append(f"{k}: {', '.join(v)}")
                else:
                    lines.append(f"{k}: {v}")
        impression = tags.get("impression", "")
        if impression:
            lines.append(f"印象: {impression}")
        return "\n".join(lines) if lines else "（无手动标签）"

    def _parse_nicknames(self) -> dict[str, list[str]]:
        """一次性把 nickname_mappings 解析为 {uid: [main, alias1, alias2, ...]}。
        第一项固定为主称呼,后续为别名。返回新 dict,避免调用方修改污染缓存。
        """
        result: dict[str, list[str]] = {}
        for item in self.config.get("nickname_mappings", []) or []:
            if not isinstance(item, str):
                continue
            uid, _, rest = item.partition(",")
            uid = uid.strip()
            if not uid or not rest.strip():
                continue
            aliases = [a.strip() for a in rest.split(",") if a.strip()]
            if aliases and uid not in result:
                result[uid] = aliases
        return result

    def _resolve_name(self, user_id: str, fallback: str) -> str:
        return self._parse_nicknames().get(user_id, [fallback])[0]

    def _get_aliases(self, user_id: str) -> list[str]:
        return list(self._parse_nicknames().get(user_id, []))

    async def get_random_persona_for_group(self, group_id: str) -> Optional[dict]:
        personas = await self.storage.get_personas_by_group(group_id)
        if not personas:
            return None
        import random
        return random.choice(personas)

    # ---------- JSON 提取辅助 ----------

    def _extract_json(self, text: str) -> Optional[dict]:
        if not text:
            return None
        text = self._strip_code_fence(text)
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except Exception:
                return None
        return None

    def _strip_code_fence(self, text: str) -> str:
        if not text:
            return text
        t = text.strip()
        if t.startswith("```"):
            lines = t.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines)
        return t
