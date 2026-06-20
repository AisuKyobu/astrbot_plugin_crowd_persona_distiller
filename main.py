from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.web import error_response, json_response, request
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, At

from .storage import GroupFriendStorage
from .persona_mgr import PersonaManager
from .reply_engine import ReplyEngine


@register("astrbot_plugin_crowd_persona_distiller", "AisuKyobu", "群友蒸馏bot：记录群聊 → LLM蒸馏人格 → 扮演群友回复", "0.1.0")
class GroupFriendPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage = GroupFriendStorage()
        self.persona_mgr = PersonaManager(context, self.storage, config)
        self.reply_engine: ReplyEngine | None = None

    async def initialize(self):
        await self.storage.init_db()
        self.reply_engine = ReplyEngine(
            self.context, self.storage, self.persona_mgr, self.config
        )
        await self.reply_engine.start_cold_detector()

        self._register_web_apis()

        logger.info("[群友蒸馏] 插件已初始化")

    async def terminate(self):
        if self.reply_engine:
            await self.reply_engine.stop_cold_detector()
        await self.storage.close()
        logger.info("[群友蒸馏] 插件已销毁")

    # ---------- 消息监听 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            return

        target_groups = self.config.get("target_groups", [])
        if target_groups and group_id not in target_groups:
            return

        if event.get_sender_id() == event.get_self_id():
            return

        if event.is_at_or_wake_command:
            return

        content = event.message_str.strip() if event.message_str else ""
        message_type = event.get_message_type()
        type_val = message_type.value if hasattr(message_type, "value") else str(message_type)
        if "other" in type_val.lower() or "notice" in type_val.lower():
            return

        if not content or len(content) < 2:
            return

        if content.startswith("/"):
            return

        try:
            group_id_str = str(group_id)
            user_id = str(event.get_sender_id())
            user_name = event.get_sender_name() or user_id

            await self.storage.record_message(group_id_str, user_id, user_name, content)

            import time
            await self.storage.update_group_state(
                group_id_str, last_message_at=time.time()
            )

            should_reply = await self.reply_engine.should_reply_on_message(event)
            if should_reply:
                await self.reply_engine.do_reply(group_id_str, event)

        except Exception as e:
            logger.error(f"[群友蒸馏] 消息处理异常: {e}")

    # ---------- CLI 指令 ----------

    @filter.command_group("qunyou")
    def qunyou_cmd_group():
        pass

    @qunyou_cmd_group.command("list")
    async def qunyou_list(self, event: AstrMessageEvent):
        """列出所有已蒸馏群友"""
        personas = self.persona_mgr.list_all()
        if not personas:
            yield event.plain_result("还没有蒸馏过任何群友。用 /qunyou distill <名字> 开始吧！")
            return

        lines = ["已蒸馏群友：", ""]
        for p in personas:
            mc = p.get("message_count", 0)
            ts = p.get("updated_at", "")[:10]
            lines.append(f"• [{p['slug']}] {p['name']} — {mc}条消息 — {ts}")
        yield event.plain_result("\n".join(lines))

    @qunyou_cmd_group.command("distill")
    async def qunyou_distill(self, event: AstrMessageEvent, name: str):
        """蒸馏指定群友"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令。")
            return

        users = await self.storage.list_active_users(str(group_id), min_messages=1)
        matched = None
        for u in users:
            uname = u.get("user_name", "")
            if name.lower() in uname.lower():
                matched = u
                break

        if not matched:
            yield event.plain_result(f"在群 {group_id} 中未找到匹配 '{name}' 的发言用户。\n请确认名字是否正确，或让该群友多发几条消息。")
            return

        user_id = matched["user_id"]
        user_name = matched["user_name"]
        count = matched["cnt"]

        yield event.plain_result(f"开始蒸馏 {user_name}（{count} 条消息），请稍候...")

        try:
            slug = await self.persona_mgr.distill(str(group_id), user_id, user_name)
            if slug:
                yield event.plain_result(f"蒸馏完成！群友 [{slug}] {user_name} 已生成 Persona。\n触发回复：/qunyou reply {slug}")
            else:
                yield event.plain_result("蒸馏失败，请检查日志。")
        except Exception as e:
            logger.error(f"[群友蒸馏] 蒸馏指令失败: {e}")
            yield event.plain_result(f"蒸馏出错: {e}")

    @qunyou_cmd_group.command("reply")
    async def qunyou_reply(self, event: AstrMessageEvent, name: str):
        """立即用指定群友的语气回复一条"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中使用此指令。")
            return

        personas = await self.storage.get_personas_by_group(str(group_id))
        matched = None
        for p in personas:
            if name.lower() in p["slug"].lower() or name.lower() in p["name"].lower():
                matched = p
                break

        if not matched:
            yield event.plain_result(f"未找到匹配的群友 '{name}'，请先蒸馏。")
            return

        result = await self.reply_engine.generate_reply(str(group_id))
        if not result:
            yield event.plain_result("生成回复失败。")
            return

        text, slug = result
        await event.send(MessageChain([Plain(text)]))
        await self.reply_engine._record_reply(str(group_id), slug)
        await self.reply_engine._maybe_change_name(str(group_id), slug, event)

    @qunyou_cmd_group.command("delete")
    async def qunyou_delete(self, event: AstrMessageEvent, name: str):
        """删除指定群友 Persona"""
        personas = self.persona_mgr.list_all()
        matched = None
        for p in personas:
            if name.lower() in p["slug"].lower() or name.lower() in p["name"].lower():
                matched = p
                break

        if not matched:
            yield event.plain_result(f"未找到匹配的群友 '{name}'。")
            return

        self.persona_mgr.delete_persona(matched["slug"])
        await self.storage.delete_persona_index(matched["slug"])

        sid = matched["user_id"]
        gid = matched["group_id"]
        await self.storage.delete_user_messages(gid, sid)
        yield event.plain_result(f"已删除群友 [{matched['slug']}] {matched['name']}。")

    # ---------- WebAPI ----------

    def _register_web_apis(self):
        p = "astrbot_plugin_crowd_persona_distiller"
        self.context.register_web_api(f"/{p}/personas", self._api_list_personas, ["GET"], "列出所有 Persona")
        self.context.register_web_api(f"/{p}/distill", self._api_distill, ["POST"], "触发蒸馏")
        self.context.register_web_api(f"/{p}/persona/<slug>", self._api_get_persona, ["GET"], "获取 Persona 内容")
        self.context.register_web_api(f"/{p}/persona/<slug>/save", self._api_save_persona, ["POST"], "保存 Persona 内容")
        self.context.register_web_api(f"/{p}/import", self._api_import, ["POST"], "导入聊天记录")
        self.context.register_web_api(f"/{p}/group_users/<group_id>", self._api_group_users, ["GET"], "群用户列表")

    async def _api_list_personas(self):
        personas = self.persona_mgr.list_all()
        return json_response(personas)

    async def _api_distill(self):
        payload = await request.json(default={})
        group_id = payload.get("group_id", "")
        user_id = payload.get("user_id", "")
        user_name = payload.get("user_name", "")
        if not group_id or not user_id:
            return error_response("missing group_id/user_id/user_name", status_code=400)
        try:
            slug = await self.persona_mgr.distill(group_id, user_id, user_name)
            if slug:
                return json_response({"slug": slug, "name": user_name})
            return error_response("distill failed", status_code=500)
        except Exception as e:
            return error_response(str(e), status_code=500)

    async def _api_get_persona(self, slug: str):
        content = self.persona_mgr.load_persona(slug)
        meta = self.persona_mgr.load_meta(slug)
        if content is None:
            return error_response("not found", status_code=404)
        return json_response({"content": content, "meta": meta})

    async def _api_save_persona(self, slug: str):
        payload = await request.json(default={})
        content = payload.get("content", "")
        if not content:
            return error_response("missing content", status_code=400)
        self.persona_mgr.save_persona(slug, content)
        return json_response({"saved": True})

    async def _api_import(self):
        files = await request.files()
        upload = files.get("file") if files else None
        if not upload:
            return error_response("missing file", status_code=400)

        from pathlib import Path
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        import tempfile
        import os
        import json as _json

        tmp_path = os.path.join(tempfile.gettempdir(), f"qy_import_{upload.filename}")
        await upload.save(tmp_path)

        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except Exception as e:
            return error_response(f"JSON 解析失败: {e}", status_code=400)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        group_id = request.query.get("group_id", "")
        target_name = request.query.get("target_name", "")

        from .chat_parser import parse_qq_export_json
        messages = parse_qq_export_json(data, target_name)
        if not messages:
            return error_response("未解析到消息", status_code=400)

        if not group_id:
            return json_response({"count": len(messages), "preview": messages[:5]})

        user_id = "imported_" + target_name
        count = await self.persona_mgr.import_from_messages(
            group_id, user_id, target_name, messages
        )
        return json_response({"imported": count, "user_name": target_name})

    async def _api_group_users(self, group_id: str):
        users = await self.storage.list_active_users(group_id, min_messages=1)
        return json_response(users)
