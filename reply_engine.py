import asyncio
import random
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from .persona_mgr import PersonaManager
from .storage import GroupFriendStorage


class ReplyEngine:
    def __init__(
        self,
        context: Context,
        storage: GroupFriendStorage,
        persona_mgr: PersonaManager,
        config: dict,
    ):
        self.context = context
        self.storage = storage
        self.persona_mgr = persona_mgr
        self.config = config
        self._cold_task: asyncio.Task | None = None
        self._persona_last_reply: dict[str, float] = {}
        self._self_id: str | None = None
        self._self_id_failed: bool = False

    # ---------- 概率触发 ----------

    async def should_reply_on_message(self, event: AstrMessageEvent) -> bool:
        group_id = event.get_group_id()
        if not group_id:
            return False

        probability = self.config.get("reply_probability", 0.05)
        if random.random() > probability:
            return False

        return await self._check_cooldowns(group_id)

    # ---------- 冷群检测 ----------

    async def start_cold_detector(self):
        if self._cold_task and not self._cold_task.done():
            return
        self._cold_task = asyncio.create_task(self._cold_loop())
        logger.info("[群友蒸馏] 冷群检测已启动")

    async def stop_cold_detector(self):
        if self._cold_task:
            self._cold_task.cancel()
            try:
                await self._cold_task
            except asyncio.CancelledError:
                pass

    async def _cold_loop(self):
        while True:
            try:
                await self._check_all_groups_for_cold()
            except Exception as e:
                logger.error(f"[群友蒸馏] 冷群检测异常: {e}")
            await asyncio.sleep(60)

    async def _check_all_groups_for_cold(self):
        if self._in_sleep_time():
            return

        cold_hours = self.config.get("cold_group_hours", 4)
        cold_seconds = cold_hours * 3600
        now = time.time()

        all_personas = await self.storage.get_all_personas()
        checked_groups = set()
        for p in all_personas:
            gid = p["group_id"]
            if gid in checked_groups:
                continue
            checked_groups.add(gid)

            state = await self.storage.get_group_state(gid)
            if not state or not state.get("last_message_at"):
                continue
            if not state.get("enabled"):
                continue

            if now - state["last_message_at"] < cold_seconds:
                continue

            if not await self._check_cooldowns(gid):
                continue

            logger.info(f"[群友蒸馏] 群 {gid} 冷群触发，进行扮演回复")
            await self.do_reply(gid, event=None, is_cold=True)

    def _in_sleep_time(self) -> bool:
        sleep_start = self.config.get("cold_group_sleep_start", 2)
        sleep_end = self.config.get("cold_group_sleep_end", 8)
        hour = datetime.now().hour
        if sleep_start < sleep_end:
            return sleep_start <= hour < sleep_end
        else:
            return hour >= sleep_start or hour < sleep_end

    # ---------- 回复生成 ----------

    async def generate_reply(self, group_id: str, is_cold: bool = False) -> tuple[str, str] | None:
        # 读群配置
        cfg = await self.storage.get_group_config(group_id)
        if cfg.get("reply_mode") == "disabled":
            return None

        if cfg.get("reply_mode") == "specific" and cfg.get("specific_slug"):
            idx = await self.storage.get_persona_index(cfg["specific_slug"])
            if idx:
                persona = {
                    "slug": idx["slug"],
                    "name": idx["name"],
                    "group_id": idx["group_id"],
                }
            else:
                persona = await self.persona_mgr.get_random_persona_for_group(group_id)
        else:
            persona = await self.persona_mgr.get_random_persona_for_group(group_id)

        if not persona:
            logger.warning(f"[群友蒸馏] 群 {group_id} 没有可用的 persona")
            return None

        slug = persona["slug"]
        reply_blacklist = self.config.get("reply_blacklist", [])
        if slug in reply_blacklist:
            return None

        persona_content = self.persona_mgr.load_persona(slug)
        if not persona_content:
            logger.warning(f"[群友蒸馏] persona.md 不存在: {slug}")
            return None

        provider_id = self.config.get("reply_provider", "")
        if not provider_id:
            logger.error("[群友蒸馏] 未配置 reply_provider")
            return None

        context_count = self.config.get("context_message_count", 20)
        recent = await self.storage.get_recent_messages(group_id, limit=context_count)
        history_text = self._format_context(recent)

        extra_prompt = self.config.get("custom_reply_system_prompt", "")
        system_prompt = persona_content
        if is_cold:
            cold_hours = self.config.get("cold_group_hours", 4)
            system_prompt = (
                f"{cold_hours}小时没有人说话了，请你结合性格和群消息自己开启一个话题或是回复一段话。"
                f"\n\n{persona_content}"
            )
        if extra_prompt:
            system_prompt += f"\n\n## 额外指令\n{extra_prompt}"

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=history_text,
                system_prompt=system_prompt,
            )
            text = llm_resp.completion_text.strip() if llm_resp else ""
            if not text:
                return None
            return text, slug
        except Exception as e:
            logger.error(f"[群友蒸馏] LLM 回复生成失败: {e}")
            return None

    async def do_reply(self, group_id: str, event: AstrMessageEvent | None, is_cold: bool = False):
        personas = await self.storage.get_personas_by_group(group_id)
        if not personas:
            if event:
                try:
                    await event.send(
                        __import__("astrbot.api.event", fromlist=["MessageChain"])
                        .MessageChain()
                        .message("当前还没有群友的人格信息哦，请先在web面板进行蒸馏~")
                    )
                except Exception:
                    pass
            return

        result = await self.generate_reply(group_id, is_cold=is_cold)
        if not result:
            return

        text, slug = result

        try:
            if event:
                await event.send(
                    __import__("astrbot.api.event", fromlist=["MessageChain"])
                    .MessageChain()
                    .message(text)
                )
            else:
                from astrbot.api.event import MessageChain

                umo = f"aiocqhttp:group_message:{group_id}"
                await self.context.send_message(umo, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[群友蒸馏] 发送回复失败: {e}")
            return

        await self._record_reply(group_id, slug)
        await self._maybe_change_name(group_id, slug, event)

    # ---------- 内部方法 ----------

    async def _check_cooldowns(self, group_id: str) -> bool:
        state = await self.storage.get_group_state(group_id)
        if not state:
            return True

        now = time.time()
        cooldown = self.config.get("reply_cooldown_minutes", 120) * 60
        if (
            state.get("last_bot_reply_at")
            and now - state["last_bot_reply_at"] < cooldown
        ):
            return False
        return True

    async def _record_reply(self, group_id: str, slug: str):
        now = time.time()
        await self.storage.update_group_state(group_id, last_bot_reply_at=now)
        self._persona_last_reply[slug] = now

    async def _get_self_id(self, event: AstrMessageEvent | None) -> str | None:
        if event:
            sid = event.get_self_id()
            if sid:
                return sid

        if self._self_id:
            return self._self_id
        if self._self_id_failed:
            return None

        platforms = getattr(self.context, "platform_manager", None)
        if not platforms:
            self._self_id_failed = True
            return None

        for plat in platforms:
            bot = getattr(plat, "bot", None)
            if not bot or not hasattr(bot, "call_action"):
                continue
            try:
                info = await bot.call_action("get_login_info")
                sid = str(info.get("user_id", ""))
                if sid:
                    self._self_id = sid
                    return sid
            except Exception:
                continue

        self._self_id_failed = True
        return None

    async def _maybe_change_card(
        self, group_id: str, slug: str, event: AstrMessageEvent | None
    ):
        cfg = await self.storage.get_group_config(group_id)
        if not cfg.get("enable_name_change", True):
            return

        persona_idx = await self.storage.get_persona_index(slug)
        if not persona_idx:
            return
        new_card = self._resolve_name(
            persona_idx.get("user_id", ""), persona_idx.get("name", "")
        )
        if not new_card:
            return
        self_id = await self._get_self_id(event)
        if not self_id:
            logger.warning("[群友蒸馏] 无法获取 Bot 自身 ID，跳过改名片")
            return

        bot = None
        if event and hasattr(event, "bot"):
            bot = event.bot
        else:
            platforms = getattr(self.context, "platform_manager", None)
            if platforms:
                for plat in platforms:
                    b = getattr(plat, "bot", None)
                    if b and hasattr(b, "call_action"):
                        bot = b
                        break

        if not bot or not hasattr(bot, "call_action"):
            return

        try:
            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(self_id),
                card=new_card,
            )
            logger.info(f"[群友蒸馏] 群 {group_id} 名片已改为: {new_card}")
        except Exception as e:
            logger.warning(f"[群友蒸馏] 改名片失败: {e}")

    async def _maybe_change_name(
        self, group_id: str, slug: str, event: AstrMessageEvent | None
    ):
        await self._maybe_change_card(group_id, slug, event)

    def _format_context(self, messages: list[dict]) -> str:
        if not messages:
            return "（暂无聊天记录）"
        ref = self._build_reference(messages)
        lines = [ref, "以下是最近的群聊天记录，请用你的语气自然地回复：", ""]
        for m in messages:
            uid = m.get("user_id", "")
            name = self._resolve_name(uid, m.get("user_name", ""))
            content = m["content"]
            lines.append(f"{name}（QQ: {uid}）: {content}")
        return "\n".join(lines)

    def _resolve_name(self, user_id: str, fallback: str) -> str:
        for item in self.config.get("nickname_mappings", []):
            if isinstance(item, str):
                uid, sep, rest = item.partition(",")
                if uid.strip() == user_id and rest.strip():
                    return rest.split(",")[0].strip()
        return fallback

    def _build_reference(self, messages: list[dict]) -> str:
        import re

        uids = set()
        alias_to_qq = self._build_alias_index()

        for m in messages:
            uid = m.get("user_id", "")
            if uid:
                uids.add(uid)

            content = m.get("content", "")
            for match in re.finditer(r'@QQ(\d+)', content):
                uids.add(match.group(1))
            for match in re.finditer(r'@(\S+?)\((\d+)\)', content):
                uids.add(match.group(2))
            for alias, qq in alias_to_qq.items():
                if alias in content:
                    uids.add(qq)

        fallback = self._build_fallback(messages)

        refs = []
        for uid in sorted(uids):
            aliases = self._get_aliases(uid)
            if aliases:
                refs.append(f"- {uid}: {', '.join(aliases)}")
            elif uid in fallback:
                refs.append(f"- {uid}: {fallback[uid]}（群昵称）")

        if not refs:
            return ""
        return (
            "## 群友称呼参考\n"
            "以下为该群友在群中的昵称、外号等称呼：\n\n"
            + "\n".join(refs) + "\n\n"
        )

    def _build_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for item in self.config.get("nickname_mappings", []):
            if isinstance(item, str):
                uid, sep, rest = item.partition(",")
                uid = uid.strip()
                if uid and rest.strip():
                    for a in rest.split(","):
                        a = a.strip()
                        if a and a not in index:
                            index[a] = uid
        return index

    def _build_fallback(self, messages: list[dict]) -> dict[str, str]:
        fb: dict[str, str] = {}
        for m in messages:
            uid = m.get("user_id", "")
            uname = m.get("user_name", "")
            if uid and uname and uid not in fb:
                fb[uid] = uname
        return fb

    def _get_aliases(self, user_id: str) -> list[str]:
        for item in self.config.get("nickname_mappings", []):
            if isinstance(item, str):
                uid, sep, rest = item.partition(",")
                if uid.strip() == user_id and rest.strip():
                    return [a.strip() for a in rest.split(",") if a.strip()]
        return []
