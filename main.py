from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from quart import jsonify, request

from .persona_mgr import PersonaManager
from .reply_engine import ReplyEngine
from .storage import GroupFriendStorage


def _json(data, status_code=200):
    return jsonify(data), status_code


def _err(msg, status_code=400):
    return jsonify({"status": "error", "message": msg}), status_code


@register(
    "astrbot_plugin_crowd_persona_distiller",
    "AisuKyobu",
    "群友蒸馏bot：记录群聊 → LLM蒸馏人格 → 扮演群友回复",
    "0.1.0",
)
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
        type_val = (
            message_type.value if hasattr(message_type, "value") else str(message_type)
        )
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
            yield event.plain_result(
                "还没有蒸馏过任何群友。用 /qunyou distill <名字> 开始吧！"
            )
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
            yield event.plain_result(
                f"在群 {group_id} 中未找到匹配 '{name}' 的发言用户。\n请确认名字是否正确，或让该群友多发几条消息。"
            )
            return

        user_id = matched["user_id"]
        user_name = matched["user_name"]
        count = matched["cnt"]

        yield event.plain_result(f"开始蒸馏 {user_name}（{count} 条消息），请稍候...")

        try:
            slug = await self.persona_mgr.distill(str(group_id), user_id, user_name)
            if slug:
                yield event.plain_result(
                    f"蒸馏完成！群友 [{slug}] {user_name} 已生成 Persona。\n触发回复：/qunyou reply {slug}"
                )
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
        for route_prefix in (f"/{p}", ""):
            self.context.register_web_api(
                f"{route_prefix}/personas", self._api_list_personas, ["GET"], "列出所有 Persona"
            )
            self.context.register_web_api(
                f"{route_prefix}/distill", self._api_distill, ["POST"], "触发蒸馏"
            )
            self.context.register_web_api(
                f"{route_prefix}/persona/<slug>", self._api_get_persona, ["GET"], "获取 Persona 内容"
            )
            self.context.register_web_api(
                f"{route_prefix}/persona/<slug>/save",
                self._api_save_persona,
                ["POST"],
                "保存 Persona 内容",
            )
            self.context.register_web_api(
                f"{route_prefix}/import/preview",
                self._api_import_preview,
                ["POST"],
                "上传聊天记录，返回解析摘要",
            )
            self.context.register_web_api(
                f"{route_prefix}/import/execute",
                self._api_import_execute,
                ["POST"],
                "确认导入选中的群友消息",
            )
            self.context.register_web_api(
                f"{route_prefix}/group_users/<group_id>", self._api_group_users, ["GET"], "群用户列表"
            )

    async def _api_list_personas(self):
        personas = self.persona_mgr.list_all()
        return _json(personas)

    async def _api_distill(self):
        payload = (await request.get_json()) or {}
        group_id = payload.get("group_id", "")
        user_id = payload.get("user_id", "")
        user_name = payload.get("user_name", "")
        if not group_id or not user_id:
            return _err("missing group_id/user_id/user_name", status_code=400)
        try:
            slug = await self.persona_mgr.distill(group_id, user_id, user_name)
            if slug:
                return _json({"slug": slug, "name": user_name})
            return _err("distill failed", status_code=500)
        except Exception as e:
            return _err(str(e), status_code=500)

    async def _api_get_persona(self, slug: str):
        content = self.persona_mgr.load_persona(slug)
        meta = self.persona_mgr.load_meta(slug)
        if content is None:
            return _err("not found", status_code=404)
        return _json({"content": content, "meta": meta})

    async def _api_save_persona(self, slug: str):
        payload = (await request.get_json()) or {}
        content = payload.get("content", "")
        if not content:
            return _err("missing content", status_code=400)
        self.persona_mgr.save_persona(slug, content)
        return _json({"saved": True})

    async def _api_import_preview(self):
        import base64
        import json as _json_lib
        import uuid

        filename = ""
        body = None
        try:
            content_type = (request.content_type or "").lower()
            logger.info(f"[群友蒸馏] import preview Content-Type: {content_type}")

            if "multipart" in content_type:
                files = request.files
                upload = files.get("file")
                if upload is None:
                    return _err("未找到上传文件", status_code=400)
                body = upload.read()
                filename = getattr(upload, "filename", "")
            elif "json" in content_type:
                payload = (await request.get_json()) or {}
                file_content = payload.get("file_content", "")
                filename = payload.get("file_name", "")
                if not file_content:
                    return _err("缺少 file_content 字段", status_code=400)
                body = base64.b64decode(file_content)
            else:
                body = await request.get_data()
                try:
                    payload = _json_lib.loads(body)
                    if isinstance(payload, dict) and "file_content" in payload:
                        body = base64.b64decode(payload["file_content"])
                        filename = payload.get("file_name", "")
                except Exception:
                    pass

            if not body:
                return _err("请求体为空", status_code=400)

            data = _json_lib.loads(body)
        except Exception as e:
            logger.error(f"[群友蒸馏] 导入文件解析失败: {e}", exc_info=True)
            return _err(f"文件解析失败: {e}", status_code=400)

        try:
            from .chat_parser import extract_import_summary

            summary = extract_import_summary(data, filename=filename)
            if not summary.get("users"):
                return _err("未从文件中解析到任何用户消息", status_code=400)

            token = uuid.uuid4().hex
            await self.put_kv_data(f"import_{token}", data)
            summary["import_token"] = token

            logger.info(
                f"[群友蒸馏] 导入预览: {summary['total_messages']} 条消息, "
                f"{len(summary['users'])} 个用户, "
                f"群: {summary.get('group_name', '未知')}"
            )
            return _json(summary)
        except Exception as e:
            logger.error(f"[群友蒸馏] 导入摘要提取失败: {e}")
            return _err(f"摘要提取失败: {e}", status_code=500)

    async def _api_import_execute(self):

        try:
            payload = (await request.get_json()) or {}
        except Exception:
            return _err("invalid JSON body", status_code=400)

        token = payload.get("import_token", "")
        group_id = payload.get("group_id", "")
        user_ids = payload.get("user_ids", [])

        if not token:
            return _err("missing import_token", status_code=400)
        if not group_id:
            return _err("missing group_id", status_code=400)
        if not user_ids:
            return _err("missing user_ids", status_code=400)

        data = await self.get_kv_data(f"import_{token}")
        if not data:
            return _err("import token 已过期，请重新上传文件", status_code=400)

        try:
            from .chat_parser import parse_qq_export_json

            results = []
            for uid in user_ids:
                user_name = ""
                messages = parse_qq_export_json(data, uid)
                if not messages:
                    messages = parse_qq_export_json(data, "")  # fallback: 按名字匹配
                    filtered = [
                        m
                        for m in messages
                        if m.get("sender") == uid or m.get("sender", "").startswith(uid)
                    ]
                    if filtered:
                        messages = filtered
                        user_name = uid
                else:
                    user_name = uid

                if messages:
                    count = await self.persona_mgr.import_from_messages(
                        group_id, uid, user_name, messages
                    )
                    results.append(
                        {"user_id": uid, "user_name": user_name, "imported": count}
                    )

            await self.delete_kv_data(f"import_{token}")
            logger.info(f"[群友蒸馏] 导入完成: {len(results)} 个用户, 群 {group_id}")
            return _json({"results": results, "group_id": group_id})
        except Exception as e:
            logger.error(f"[群友蒸馏] 导入执行失败: {e}")
            return _err(f"导入执行失败: {e}", status_code=500)

    async def _api_group_users(self, group_id: str):
        users = await self.storage.list_active_users(group_id, min_messages=1)
        return _json(users)
