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
            await self.do_reply(gid, event=None)

    def _in_sleep_time(self) -> bool:
        sleep_start = self.config.get("cold_group_sleep_start", 2)
        sleep_end = self.config.get("cold_group_sleep_end", 8)
        hour = datetime.now().hour
        if sleep_start < sleep_end:
            return sleep_start <= hour < sleep_end
        else:
            return hour >= sleep_start or hour < sleep_end

    # ---------- 回复生成 ----------

    async def generate_reply(self, group_id: str) -> tuple[str, str] | None:
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

    async def do_reply(self, group_id: str, event: AstrMessageEvent | None):
        result = await self.generate_reply(group_id)
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

    async def _maybe_change_name(
        self, group_id: str, slug: str, event: AstrMessageEvent | None
    ):
        if not self.config.get("enable_name_change", True):
            return

        blacklist = self.config.get("name_change_blacklist", [])
        if slug in blacklist:
            return

        state = await self.storage.get_group_state(group_id)
        cooldown = self.config.get("name_change_cooldown_hours", 1) * 3600
        now = time.time()
        if state and state.get("last_name_change_at"):
            if now - state["last_name_change_at"] < cooldown:
                return

        persona_idx = await self.storage.get_persona_index(slug)
        if not persona_idx:
            return
        new_name = persona_idx["name"]

        try:
            if event and hasattr(event, "bot"):
                await event.bot.set_group_name(
                    group_id=int(group_id), group_name=new_name
                )
            else:
                platforms = getattr(self.context, "platform_manager", None)
                if platforms:
                    for plat in platforms:
                        if hasattr(plat, "bot") and hasattr(plat.bot, "set_group_name"):
                            await plat.bot.set_group_name(
                                group_id=int(group_id), group_name=new_name
                            )
                            break
            await self.storage.update_group_state(group_id, last_name_change_at=now)
            logger.info(f"[群友蒸馏] 群 {group_id} 名称已改为: {new_name}")
        except Exception as e:
            logger.warning(f"[群友蒸馏] 改群名失败: {e}")

    def _format_context(self, messages: list[dict]) -> str:
        if not messages:
            return "（暂无聊天记录）"
        lines = ["以下是最近的群聊天记录，请用你的语气自然地回复：", ""]
        for m in messages:
            name = m["user_name"]
            content = m["content"]
            lines.append(f"{name}: {content}")
        return "\n".join(lines)
