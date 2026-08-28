import asyncio
import random
import re
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from .persona_mgr import PersonaManager
from .storage import GroupFriendStorage


_AT_PATTERN = re.compile(r"@([^\s@,，:：()（）]+)(?:[(（](\d+)[)）])?")
_AT_NICK_QQ_PATTERN = re.compile(r"@(\S+?)\((\d+)\)")
_AT_QQ_PATTERN = re.compile(r"@QQ(\d+)")
_CQ_AT_PATTERN = re.compile(r"\[CQ:at,qq=(\d+)\]")


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

    async def generate_reply(
        self,
        group_id: str,
        is_cold: bool = False,
        latest_image_urls: list[str] | None = None,
    ) -> tuple[str, str] | None:
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

        # persona-level cooldown: re-pick a different persona if recently used
        persona_cd = self.config.get("persona_cooldown_minutes", 30) * 60
        if persona_cd > 0:
            now_cd = time.time()
            if self._persona_last_reply.get(slug) and (now_cd - self._persona_last_reply[slug]) < persona_cd:
                pool = await self.storage.get_personas_by_group(group_id)
                pool = [p for p in pool if p["slug"] != slug and p["slug"] not in reply_blacklist]
                pool = [p for p in pool if not self._persona_last_reply.get(p["slug"]) or (now_cd - self._persona_last_reply[p["slug"]]) >= persona_cd]
                if not pool:
                    return None
                persona = random.choice(pool)
                slug = persona["slug"]

        persona_content = self.persona_mgr.load_persona(slug)
        if not persona_content:
            logger.warning(f"[群友蒸馏] persona.md 不存在: {slug}")
            return None

        provider_id = self.config.get("reply_provider", "")
        if not provider_id:
            logger.error("[群友蒸馏] 未配置 reply_provider")
            return None

        context_count = self.config.get("context_message_count", 20)
        state = await self.storage.get_group_state(group_id)
        last_reply_at = state.get("last_bot_reply_at") if state else 0
        if last_reply_at:
            recent = await self.storage.get_recent_messages_since(
                group_id, last_reply_at, limit=context_count
            )
        else:
            recent = await self.storage.get_recent_messages(group_id, limit=context_count)
        history_text = self._format_context(recent)

        # 载入 reply_directives
        directives_path = Path(__file__).parent / "prompts" / "reply_directives.md"
        try:
            directives = directives_path.read_text(encoding="utf-8")
        except Exception:
            directives = ""

        extra_prompt = self.config.get("custom_reply_system_prompt", "")
        system_prompt = persona_content

        if is_cold:
            cold_hours = self.config.get("cold_group_hours", 4)
            cold_intro = (
                f"[cold_group_trigger] 当前是冷群开场：群 {cold_hours} 小时无人发言。"
                f"请参考 reply_directives 的'冷群开场'段。"
            )
            system_prompt = f"{cold_intro}\n\n{persona_content}"

        if directives:
            system_prompt += f"\n\n---\n\n{directives}"
        if extra_prompt:
            system_prompt += f"\n\n## 用户额外指令\n{extra_prompt}"

        # 决定要传的图片 URL:优先用 latest_image_urls 参数,否则从 recent 最后一条取
        image_urls: list[str] = []
        if latest_image_urls:
            image_urls = [u for u in latest_image_urls if isinstance(u, str) and u]
        elif recent:
            try:
                import json as _json
                raw = recent[-1].get("image_urls") or "[]"
                parsed = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list):
                    image_urls = [u for u in parsed if isinstance(u, str) and u]
            except Exception:
                image_urls = []

        # 配置:image_fallback_to_base64=True 时把 URL 下载转 base64,避免 LLM provider 拉不到 QQ CDN
        if image_urls and self.config.get("image_fallback_to_base64", False):
            image_urls = await self._resolve_image_urls_to_base64(image_urls)

        # 有图则优先用 vision_provider,留空时 fallback 到 reply_provider
        if image_urls:
            provider_id = (
                self.config.get("vision_provider")
                or self.config.get("reply_provider", "")
            )
        else:
            provider_id = self.config.get("reply_provider", "")
        if not provider_id:
            logger.error("[群友蒸馏] 未配置 reply_provider")
            return None

        try:
            kwargs = {
                "chat_provider_id": provider_id,
                "prompt": history_text,
                "system_prompt": system_prompt,
            }
            if image_urls:
                kwargs["image_urls"] = image_urls
            llm_resp = await self.context.llm_generate(**kwargs)
            text = llm_resp.completion_text.strip() if llm_resp else ""
            if not text:
                return None
            return text, slug
        except Exception as e:
            logger.warning(f"[群友蒸馏] LLM 回复生成失败(可能是不支持 vision): {e}")
            if image_urls:
                # 降级:重试无图版本
                try:
                    kwargs2 = {
                        "chat_provider_id": provider_id,
                        "prompt": history_text,
                        "system_prompt": system_prompt,
                    }
                    llm_resp = await self.context.llm_generate(**kwargs2)
                    text = llm_resp.completion_text.strip() if llm_resp else ""
                    if not text:
                        return None
                    return text, slug
                except Exception as e2:
                    logger.error(f"[群友蒸馏] LLM 无图重试也失败: {e2}")
                    return None
            return None

    async def do_reply(
        self,
        group_id: str,
        event: AstrMessageEvent | None,
        is_cold: bool = False,
        latest_image_urls: list[str] | None = None,
    ):
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

        result = await self.generate_reply(
            group_id, is_cold=is_cold, latest_image_urls=latest_image_urls
        )
        if not result:
            return

        text, slug = result

        # 解析 @昵称,转成 AstrBot At 消息链
        chain = self._build_reply_chain(group_id, text)
        if chain is None:
            # 解析失败回退到纯文本
            chain = self._make_plain_chain(text)

        try:
            if event:
                await event.send(chain)
            else:
                from astrbot.api.event import MessageChain
                # context.send_message expects MessageChain — convert if needed
                if isinstance(chain, MessageChain):
                    umo = f"aiocqhttp:group_message:{group_id}"
                    await self.context.send_message(umo, chain)
                else:
                    umo = f"aiocqhttp:group_message:{group_id}"
                    await self.context.send_message(umo, MessageChain().message(text))
        except Exception as e:
            logger.error(f"[群友蒸馏] 发送回复失败: {e}")
            return

        await self._record_reply(group_id, slug)
        await self._maybe_change_name(group_id, slug, event)

    # ---------- 内部方法 ----------

    async def _check_cooldowns(self, group_id: str, slug: str = "") -> bool:
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

        # persona-level cooldown
        if slug:
            persona_cd = self.config.get("persona_cooldown_minutes", 30) * 60
            last = self._persona_last_reply.get(slug)
            if last and (now - last) < persona_cd:
                return False
        return True

    async def _record_reply(self, group_id: str, slug: str):
        now = time.time()
        await self.storage.update_group_state(group_id, last_bot_reply_at=now)
        self._persona_last_reply[slug] = now

    async def _resolve_image_urls_to_base64(self, urls: list[str]) -> list[str]:
        """下载 URL 转 base64 data URI;失败的 URL 保留原样,由调用方决定是否丢掉。"""
        if not urls:
            return urls
        try:
            import aiohttp
            import base64 as _b64
        except ImportError:
            logger.warning("[群友蒸馏] 缺少 aiohttp,无法转 base64,保留原 URL")
            return urls
        out: list[str] = []
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in urls:
                if not isinstance(url, str) or not url:
                    continue
                if url.startswith("data:") or url.startswith("base64://"):
                    out.append(url)
                    continue
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            logger.warning(f"[群友蒸馏] 图片下载失败 HTTP {resp.status}: {url}")
                            out.append(url)
                            continue
                        data = await resp.read()
                        ctype = resp.headers.get("Content-Type", "image/jpeg")
                        b64 = _b64.b64encode(data).decode("ascii")
                        out.append(f"data:{ctype};base64,{b64}")
                except Exception as e:
                    logger.warning(f"[群友蒸馏] 图片下载异常({url}): {e}")
                    out.append(url)
        return out

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
        lines = [ref]

        # 把"最新一条"提到独立 section,作为强制锚点
        latest = messages[-1]
        latest_uid = latest.get("user_id", "")
        latest_name = self._resolve_name(latest_uid, latest.get("user_name", ""))
        latest_content = latest.get("content", "") or "(空)"
        latest_at_targets = self._extract_at_targets(latest_content, latest_uid)
        at_str = ""
        if latest_at_targets:
            at_str = " " + " ".join(f"@{n}" for n in latest_at_targets)

        lines.append("")
        lines.append("## 最新一条消息(你必须直接回应这条)")
        lines.append(f"{latest_name}（QQ: {latest_uid}）{at_str}: {latest_content}")
        lines.append("")

        # 早于最新一条的消息作为氛围上下文
        earlier = messages[:-1]
        if earlier:
            lines.append("## 近期上下文(仅供理解氛围,可忽略)")
            for m in earlier:
                uid = m.get("user_id", "")
                name = self._resolve_name(uid, m.get("user_name", ""))
                content = m.get("content", "") or ""
                lines.append(f"[较早] {name}（QQ: {uid}）: {content}")
        return "\n".join(lines)

    def _extract_at_targets(self, content: str, sender_uid: str) -> list[str]:
        """从消息文本里抽出 @ 目标(去掉自己的 @)。返回 main nick 列表。"""
        targets = []
        # @昵称(123) / @昵称 这种
        for m in _AT_PATTERN.finditer(content):
            name = m.group(1).strip()
            targets.append(name)
        # [CQ:at,qq=xxx] 兼容导入数据
        nicknames = self._parse_nicknames()
        uid_to_main = {u: lst[0] for u, lst in nicknames.items()}
        for m in _CQ_AT_PATTERN.finditer(content):
            uid = m.group(1)
            if uid in uid_to_main:
                targets.append(uid_to_main[uid])
        # 去掉 @ 自己
        self_nicks: set[str] = set()
        for a in nicknames.get(sender_uid, []):
            self_nicks.add(a)
        return [t for t in targets if t not in self_nicks]

    def _parse_nicknames(self) -> dict[str, list[str]]:
        """一次性把 nickname_mappings 解析为 {uid: [main, alias1, ...]}。
        第一项固定为主称呼,后续为别名。
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

    def _build_reference(self, messages: list[dict]) -> str:
        uids = set()
        alias_to_qq = self._build_alias_index()

        for m in messages:
            uid = m.get("user_id", "")
            if uid:
                uids.add(uid)

            content = m.get("content", "")
            for match in _AT_QQ_PATTERN.finditer(content):
                uids.add(match.group(1))
            for match in _AT_NICK_QQ_PATTERN.finditer(content):
                uids.add(match.group(2))
            for alias, qq in alias_to_qq.items():
                if alias in content:
                    uids.add(qq)

        fallback = self._build_fallback(messages)

        refs = []
        for uid in sorted(uids):
            aliases = self._get_aliases(uid)
            if aliases:
                main = aliases[0]
                others = aliases[1:]
                if others:
                    refs.append(f"- {uid}: **{main}**(主) | 别名: {', '.join(others)}")
                else:
                    refs.append(f"- {uid}: **{main}**(主)")
            elif uid in fallback:
                refs.append(f"- {uid}: **{fallback[uid]}**(主,群昵称)")

        if not refs:
            return ""
        return (
            "## 群友称呼参考(主 = 全名,务必用全名)\n"
            "称呼栏第一项是**主称呼(=全名)**,其他是别人平时用的简称/外号。\n"
            "**你(LLM) 任何时候提到或 @ 这些群友时,都必须用全名(主),不要用简称。**\n"
            "例如:看到 `基长(主) | 别名: 基长是鸡` 时,这个人全名是`基长`,\n"
            "但如果看到 `基长是鸡(主) | 别名: 基长`,这个人全名是`基长是鸡`,\n"
            "你必须用 `基长是鸡`,**不能**简称 `基长`!\n\n"
            + "\n".join(refs) + "\n\n"
        )

    def _build_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for uid, lst in self._parse_nicknames().items():
            for a in lst:
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
        return list(self._parse_nicknames().get(user_id, []))
    # ---------- @ 解析 + MessageChain 构建 ----------

    def _make_plain_chain(self, text: str):
        """回退方案:纯文本链。"""
        try:
            from astrbot.api.event import MessageChain
            return MessageChain().message(text)
        except Exception:
            return None

    def _normalize_self_mentions(self, text: str, group_id: str) -> str:
        """
        全局别名→主名规范化(防止 bot 把"基长是鸡"简称为"基长")。

        遍历 `nickname_mappings` 中所有用户,如果某用户有别名,把他所有别名
        在文本中的出现处都替换为他的主称呼。**已带 @ 前缀的不替换**(因为 @解析
        在后面,这里不该破坏)。

        注意:这是兜底逻辑,主要靠 prompt 告诉 LLM 自己用全名;后处理保证
        至少 LLM 写错了也能自动修正过来。
        """
        if not text:
            return text

        sub_map: list[tuple[str, str]] = []
        for uid, lst in self._parse_nicknames().items():
            if len(lst) < 2:
                continue
            main = lst[0]
            for a in lst[1:]:
                if a and a != main:
                    sub_map.append((a, main))

        if not sub_map:
            return text

        # 长度倒序,长别名优先(避免"基"覆盖"基长")
        sub_map.sort(key=lambda x: -len(x[0]))

        out = text
        for alias, main in sub_map:
            # 用 @ 切分,只对非首段(已经是 @target 部分)替换,避免破坏 @xxx
            parts = out.split("@")
            new_parts = [parts[0]]
            for p in parts[1:]:
                new_parts.append(p.replace(alias, main))
            out = "@".join(new_parts)
        return out

    # ---------- @ 解析 + MessageChain 构建 ----------


    def _build_reply_chain(self, group_id: str, text: str):
        """
        把 LLM 输出文本解析为 MessageChain。
        解析规则:
          - @昵称 / @昵称(123)  → Comp.At(qq=...)  (通过 nickname_mappings 查 QQ)
          - 其它文本 → Comp.Plain
        没有 @ 时直接返回纯文本链。

        在解析前先做"别名→主名"规范化,防止 LLM 简称了"基长是鸡"为"基长"。
        """
        # 别名→主名兜底(prompt 已经告诉 LLM 用全名,这里是双保险)
        text = self._normalize_self_mentions(text, group_id)
        # 延迟导入,避免循环
        try:
            from astrbot.api import message_components as Comp
            from astrbot.api.event import MessageChain
        except Exception as e:
            logger.warning(f"[群友蒸馏] 导入 message_components 失败, 退化为纯文本: {e}")
            return self._make_plain_chain(text)

        # 切分:按 @ 提及分段
        parts: list[tuple[str, str]] = []
        last_end = 0
        for m in _AT_PATTERN.finditer(text):
            if m.start() > last_end:
                parts.append(("plain", text[last_end:m.start()]))
            nick = m.group(1).strip()
            explicit_qq = m.group(2)
            qq = explicit_qq or self._resolve_nick_to_qq(group_id, nick)
            if qq:
                parts.append(("at", str(qq)))
            else:
                # 解析不到,原样保留 @昵称
                parts.append(("plain", m.group(0)))
            last_end = m.end()
        if last_end < len(text):
            parts.append(("plain", text[last_end:]))

        # 如果没有任何 @,返回纯文本链
        if not any(p[0] == "at" for p in parts):
            return MessageChain().message(text)

        # 拼 MessageChain
        chain = MessageChain()
        for kind, val in parts:
            if kind == "at":
                chain = chain.at(qq=val)
            else:
                val_stripped = val.strip()
                if val_stripped:
                    chain = chain.message(val_stripped)
        if not chain.chain:
            chain = chain.message(text)
        return chain

    def _resolve_nick_to_qq(self, group_id: str, nick: str) -> str | None:
        """通过 nickname_mappings 把昵称/别名反查成 QQ 号。"""
        nick_norm = (nick or "").strip().lower()
        if not nick_norm:
            return None
        for uid, lst in self._parse_nicknames().items():
            aliases = [a.strip().lower() for a in lst if a.strip()]
            if nick_norm in aliases:
                return uid
        return None



