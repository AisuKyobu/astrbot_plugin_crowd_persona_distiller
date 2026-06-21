from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from quart import jsonify, request

from .persona_mgr import PersonaManager
from .reply_engine import ReplyEngine
from .storage import GroupFriendStorage

import base64 as _base64
import os
from pathlib import Path
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


def _json(data, status_code=200):
    return jsonify(data), status_code


def _err(msg, status_code=400):
    return jsonify({"status": "error", "message": msg, "code": status_code}), 200


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

        dip = self.config.get("distill_provider")
        rep = self.config.get("reply_provider")
        logger.info(f"[群友蒸馏] 当前配置: distill_provider={dip!r}, reply_provider={rep!r}")
        if not dip:
            logger.warning("[群友蒸馏] 未配置 distill_provider，蒸馏功能不可用，请前往插件设置配置")
        if not rep:
            logger.warning("[群友蒸馏] 未配置 reply_provider，回复/扮演功能不可用，请前往插件设置配置")
        logger.info("[群友蒸馏] 插件已初始化")

    async def terminate(self):
        if self.reply_engine:
            await self.reply_engine.stop_cold_detector()
        await self.storage.close()
        logger.info("[群友蒸馏] 插件已销毁")

    # ---------- 消息监听 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        '''监听所有群消息：自动记录群聊内容、概率触发扮演回复'''
        group_id = event.get_group_id()
        if not group_id:
            return

        target_groups = self.config.get("target_groups", [])
        if target_groups and group_id not in target_groups:
            return

        if event.get_sender_id() == event.get_self_id():
            return

        if event.is_at_or_wake_command:
            # 只有真正 @了 Bot 的消息才入库+回复，纯命令（如 /nickname）跳过
            self_id = event.get_self_id()
            is_at_bot = any(
                hasattr(c, "qq") and str(getattr(c, "qq", "")) == self_id
                for c in event.get_messages()
            )
            if not is_at_bot:
                return

            try:
                group_id_str = str(group_id)
                user_id = str(event.get_sender_id())
                user_name = event.get_sender_name() or user_id
                content = event.message_str.strip() or ""

                if content:
                    await self.storage.record_message(
                        group_id_str, user_id, user_name, content,
                        ts=event.message_obj.timestamp if event.message_obj else None,
                    )

                cfg = await self.storage.get_group_config(group_id_str)
                if cfg.get("at_trigger", True):
                    await self.reply_engine.do_reply(group_id_str, event)
            except Exception as e:
                logger.error(f"[群友蒸馏] @Bot触发失败: {e}")
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

            await self.storage.record_message(
                group_id_str, user_id, user_name, content,
                ts=event.message_obj.timestamp if event.message_obj else None,
            )

            import time

            await self.storage.update_group_state(
                group_id_str, last_message_at=time.time()
            )

            should_reply = await self.reply_engine.should_reply_on_message(event)
            if should_reply:
                await self.reply_engine.do_reply(group_id_str, event)

        except Exception as e:
            logger.error(f"[群友蒸馏] 消息处理异常: {e}")

    # ---------- 昵称管理 ----------

    @filter.command_group("nickname", priority=1)
    async def nickname_cmd_group(self, event: AstrMessageEvent):
        '''管理群友称呼映射（set / list / remove）'''
        pass

    @nickname_cmd_group.command("set")
    async def nickname_set(self, event: AstrMessageEvent, user_id: str, nickname: str):
        '''设置群友称呼：/nickname set <QQ号|@某人> <称呼,别名...>'''
        uid = user_id.strip()
        name = nickname.strip() if nickname else ""

        # 支持 @某人 语法：从消息链提取 At 目标的 QQ
        for c in event.get_messages():
            qq = str(getattr(c, "qq", ""))
            if qq and qq != "all":
                uid = qq
                at_name = getattr(c, "name", "") or ""
                if not name:
                    name = at_name or uid
                break

        if not uid or not name:
            yield event.plain_result("用法: /nickname set <QQ号|@某人> <称呼,别名...>\n    例: /nickname set @基长 基长,基宝,长哥")
            return

        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str)]
        existing = [i for i, m in enumerate(mappings) if m.startswith(uid + ",")]
        entry = f"{uid},{name}"
        if existing:
            mappings[existing[0]] = entry
            yield event.plain_result(f"已更新 {uid} 的称呼为: {name}")
        else:
            mappings.append(entry)
            yield event.plain_result(f"已设置 {uid} 的称呼为: {name}")
        self.config["nickname_mappings"] = mappings
        self.config.save_config()

    @nickname_cmd_group.command("list")
    async def nickname_list(self, event: AstrMessageEvent):
        '''查看所有已设置的称呼映射'''
        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str) and "," in m]
        if not mappings:
            yield event.plain_result("还没有设置任何称呼")
            return
        lines = ["称呼映射：", ""]
        for m in mappings:
            uid, _, name = m.partition(",")
            lines.append(f"  {uid.strip()} → {name.strip()}")
        yield event.plain_result("\n".join(lines))

    @nickname_cmd_group.command("remove")
    async def nickname_remove(self, event: AstrMessageEvent, user_id: str):
        '''删除指定群友的称呼映射：/nickname remove <QQ号>'''
        uid = user_id.strip()
        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str)]
        new_mappings = [m for m in mappings if not m.startswith(uid + ",")]
        if len(new_mappings) == len(mappings):
            yield event.plain_result(f"未找到 {uid} 的称呼映射")
            return
        self.config["nickname_mappings"] = new_mappings
        self.config.save_config()
        yield event.plain_result(f"已删除 {uid} 的称呼映射")

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
                f"{route_prefix}/persona/delete",
                self._api_delete_persona,
                ["POST"],
                "删除 Persona 及聊天记录",
            )
            self.context.register_web_api(
                f"{route_prefix}/persona/incremental",
                self._api_incremental_update,
                ["POST"],
                "增量更新 Persona",
            )
            self.context.register_web_api(
                f"{route_prefix}/persona/correct",
                self._api_correct_persona,
                ["POST"],
                "修正人格（根据用户反馈）",
            )
            self.context.register_web_api(
                f"{route_prefix}/import/preview",
                self._api_import_preview,
                ["POST"],
                "上传聊天记录，返回解析摘要",
            )
            self.context.register_web_api(
                f"{route_prefix}/import/chunk",
                self._api_import_chunk,
                ["POST"],
                "分片上传",
            )
            self.context.register_web_api(
                f"{route_prefix}/import/assemble",
                self._api_import_assemble,
                ["POST"],
                "分片拼接并解析",
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
            self.context.register_web_api(
                f"{route_prefix}/distillable", self._api_distillable_users, ["GET"], "所有可蒸馏/已蒸馏用户列表"
            )
            self.context.register_web_api(
                f"{route_prefix}/group_config/<group_id>", self._api_get_group_config, ["GET"], "获取群配置"
            )
            self.context.register_web_api(
                f"{route_prefix}/group_config/<group_id>", self._api_update_group_config, ["POST"], "更新群配置"
            )
            self.context.register_web_api(
                f"{route_prefix}/group_configs", self._api_list_group_configs, ["GET"], "列出所有群配置"
            )
            self.context.register_web_api(
                f"{route_prefix}/nicknames", self._api_list_nicknames, ["GET"], "列出所有称呼映射"
            )
            self.context.register_web_api(
                f"{route_prefix}/nickname", self._api_set_nickname, ["POST"], "设置/更新称呼映射"
            )
            self.context.register_web_api(
                f"{route_prefix}/nickname/delete", self._api_delete_nickname, ["POST"], "删除称呼映射"
            )

    async def _api_list_personas(self):
        personas = self.persona_mgr.list_all()
        for p in personas:
            gid = p.get("group_id", "")
            state = await self.storage.get_group_state(gid) if gid else None
            p["group_name"] = state.get("group_name", "") if state else ""
        return _json(personas)

    async def _api_distill(self):
        payload = (await request.get_json()) or {}
        group_id = payload.get("group_id", "")
        user_id = payload.get("user_id", "")
        user_name = payload.get("user_name", "")
        if not group_id or not user_id:
            return _err("缺失 group_id/user_id", status_code=400)

        if not self.config.get("distill_provider"):
            return _err("请先在插件设置中配置「蒸馏分析用 LLM 提供商」（distill_provider）")

        min_msgs = self.config.get("min_distill_messages", 50)
        count = await self.storage.get_user_all_message_count(group_id, user_id)
        if count < min_msgs:
            return _err(f"消息不足（当前 {count} 条，需 ≥{min_msgs} 条），请让该群友多发言或降低最低消息数阈值")

        try:
            slug = await self.persona_mgr.distill(group_id, user_id, user_name)
            if slug:
                return _json({"slug": slug, "name": user_name})
            return _err("蒸馏失败：LLM 分析出错，请查看服务端日志")
        except Exception as e:
            logger.error(f"[群友蒸馏] distill exception: {e}", exc_info=True)
            return _err(f"蒸馏异常：{e}")

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

    async def _api_delete_persona(self):
        payload = (await request.get_json()) or {}
        slug = payload.get("slug", "")
        group_id = payload.get("group_id", "")
        user_id = payload.get("user_id", "")
        if not slug or not group_id or not user_id:
            return _err("missing slug/group_id/user_id", status_code=400)
        self.persona_mgr.delete_persona(slug)
        await self.storage.delete_persona_index(slug)
        await self.storage.delete_user_messages(group_id, user_id)
        logger.info(f"[群友蒸馏] 已删除 persona: {slug}")
        return _json({"deleted": slug})

    async def _api_incremental_update(self):
        payload = (await request.get_json()) or {}
        slug = payload.get("slug", "")
        group_id = payload.get("group_id", "")
        user_id = payload.get("user_id", "")
        if not slug or not group_id or not user_id:
            return _err("missing slug/group_id/user_id", status_code=400)
        result = await self.persona_mgr.incremental_update(slug, group_id, user_id)
        if not result:
            return _err("增量更新失败，请查看服务端日志")
        return _json(result)

    async def _api_correct_persona(self):
        payload = (await request.get_json()) or {}
        slug = payload.get("slug", "")
        correction_text = payload.get("correction", "").strip()
        if not slug or not correction_text:
            return _err("missing slug/correction", status_code=400)

        if not self.config.get("distill_provider"):
            return _err("请先在插件设置中配置「蒸馏分析用 LLM 提供商」（distill_provider）")

        try:
            corrected = await self.persona_mgr.correct_persona(slug, correction_text)
            if not corrected:
                return _err("修正失败：LLM 调用出错，请查看服务端日志")
            return _json({"slug": slug, "corrected": True})
        except Exception as e:
            logger.error(f"[群友蒸馏] correct exception: {e}", exc_info=True)
            return _err(f"修正异常：{e}")

    async def _api_import_chunk(self):
        payload = (await request.get_json()) or {}
        upload_id = payload.get("upload_id", "")
        chunk_index = payload.get("chunk_index", -1)
        file_name = payload.get("file_name", "")
        data = payload.get("data", "")

        if not upload_id or data is None:
            return _err("missing upload_id or data", status_code=400)

        temp_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_crowd_persona_distiller" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = temp_dir / f"{upload_id}.part"

        with open(chunk_path, "ab" if chunk_index > 0 else "wb") as f:
            f.write(data.encode("utf-8"))

        meta_path = temp_dir / f"{upload_id}.meta"
        if not meta_path.exists():
            meta_path.write_text(file_name, encoding="utf-8")

        logger.debug(f"[群友蒸馏] chunk {chunk_index + 1} received for {upload_id}")
        return _json({"ok": True, "chunk_index": chunk_index})

    async def _api_import_assemble(self):
        payload = (await request.get_json()) or {}
        upload_id = payload.get("upload_id", "")
        if not upload_id:
            return _err("missing upload_id", status_code=400)

        import json as _json_lib

        temp_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_crowd_persona_distiller" / "temp"
        chunk_path = temp_dir / f"{upload_id}.part"
        meta_path = temp_dir / f"{upload_id}.meta"

        if not chunk_path.exists():
            return _err("upload not found or expired", status_code=400)

        try:
            base64_text = chunk_path.read_text(encoding="utf-8")
            body = _base64.b64decode(base64_text)
            data = _json_lib.loads(body)
        except Exception as e:
            self._cleanup_upload(upload_id)
            logger.error(f"[群友蒸馏] assemble decode failed: {e}")
            return _err(f"文件解析失败: {e}", status_code=400)

        try:
            from .chat_parser import extract_import_summary

            filename = meta_path.read_text(encoding="utf-8") if meta_path.exists() else ""
            summary = extract_import_summary(data, filename=filename)
        except Exception as e:
            self._cleanup_upload(upload_id)
            logger.error(f"[群友蒸馏] assemble summary failed: {e}")
            return _err(f"摘要提取失败: {e}", status_code=500)

        if not summary.get("users"):
            self._cleanup_upload(upload_id)
            return _err("未从文件中解析到任何用户消息", status_code=400)

        import uuid
        token = uuid.uuid4().hex
        await self.put_kv_data(f"import_{token}", data)
        summary["import_token"] = token

        self._cleanup_upload(upload_id)

        logger.info(
            f"[群友蒸馏] 导入预览: {summary['total_messages']} 条消息, "
            f"{len(summary['users'])} 个用户, "
            f"群: {summary.get('group_name', '未知')}"
        )
        return _json(summary)

    def _cleanup_upload(self, upload_id: str):
        temp_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_crowd_persona_distiller" / "temp"
        for ext in (".part", ".meta"):
            p = temp_dir / f"{upload_id}{ext}"
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

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
        chat_type = payload.get("chat_type", "group")

        if not token:
            return _err("missing import_token", status_code=400)
        if not group_id:
            return _err("missing group_id", status_code=400)
        if not user_ids:
            return _err("missing user_ids", status_code=400)

        data = await self.get_kv_data(f"import_{token}", None)
        if not data:
            return _err("import token 已过期，请重新上传文件", status_code=400)

        try:
            from .chat_parser import parse_qq_export_json

            # 存储群名称（仅群聊）
            if isinstance(data, dict) and chat_type == "group":
                chat_info = data.get("chatInfo") or {}
                gname = str(chat_info.get("name", "")) if isinstance(chat_info, dict) else ""
                if gname:
                    await self.storage.update_group_state(group_id, group_name=gname)

            results = []
            for uid in user_ids:
                user_name = uid
                messages = parse_qq_export_json(data, uid)
                if not messages:
                    messages = parse_qq_export_json(data, "")
                    filtered = [
                        m
                        for m in messages
                        if m.get("sender") == uid or m.get("sender", "").startswith(uid)
                    ]
                    if filtered:
                        messages = filtered
                        user_name = filtered[0].get("sender", uid)
                elif messages:
                    user_name = messages[0].get("sender", uid)

                if messages:
                    count = await self.persona_mgr.import_from_messages(
                        group_id, uid, user_name, messages, chat_type=chat_type
                    )
                    results.append(
                        {"user_id": uid, "user_name": user_name, "imported": count}
                    )
                    logger.info(f"[群友蒸馏] import execute: {user_name}({uid}) -> {count} msgs (chat_type={chat_type})")

            await self.delete_kv_data(f"import_{token}")
            logger.info(f"[群友蒸馏] 导入完成: {len(results)} 个用户, 群 {group_id}")
            return _json({"results": results, "group_id": group_id})
        except Exception as e:
            logger.error(f"[群友蒸馏] 导入执行失败: {e}")
            return _err(f"导入执行失败: {e}", status_code=500)

    async def _api_group_users(self, group_id: str):
        users = await self.storage.list_active_users(group_id, min_messages=1)
        return _json(users)

    async def _api_distillable_users(self):
        min_messages = self.config.get("min_distill_messages", 50)
        users = await self.storage.list_distillable_users(min_messages=0)

        # 获取群名称映射
        group_names = {}
        for u in users:
            gid = u["group_id"]
            if gid not in group_names:
                state = await self.storage.get_group_state(gid)
                group_names[gid] = state.get("group_name", "") if state else ""

        personas = {p["slug"]: p for p in self.persona_mgr.list_all()}

        results = []
        for u in users:
            uid = u["user_id"]
            slug = u.get("slug") or ""
            gid = u["group_id"]
            p = personas.get(slug, {})
            results.append({
                "group_id": gid,
                "group_name": group_names.get(gid, ""),
                "user_id": uid,
                "user_name": u["user_name"],
                "name": p.get("name") or u["user_name"],
                "message_count": u["message_count"],
                "last_msg_at": u.get("last_msg_at"),
                "distilled": u["distilled"],
                "slug": slug,
                "last_distill_at": p.get("last_distill_at") or "",
                "reached_threshold": u["message_count"] >= min_messages,
            })
        return _json(results)

    async def _api_get_group_config(self, group_id: str):
        cfg = await self.storage.get_group_config(group_id)
        personas = await self.storage.get_personas_by_group(group_id)
        cfg["personas"] = [
            {"slug": p["slug"], "name": p["name"]} for p in personas
        ]
        return _json(cfg)

    async def _api_update_group_config(self, group_id: str):
        payload = (await request.get_json()) or {}
        allowed = {"reply_mode", "specific_slug", "at_trigger", "enable_name_change"}
        updates = {k: v for k, v in payload.items() if k in allowed}
        if not updates:
            return _err("no valid config keys", status_code=400)
        if "at_trigger" in updates:
            updates["at_trigger"] = int(bool(updates["at_trigger"]))
        await self.storage.update_group_config(group_id, **updates)
        return _json({"saved": True})

    async def _api_list_group_configs(self):
        groups = await self.storage.list_all_groups()
        result = []
        for g in groups:
            result.append({
                "group_id": g["group_id"],
                "group_name": g.get("group_name", ""),
                "reply_mode": g.get("reply_mode", "random"),
                "specific_slug": g.get("specific_slug", ""),
                "at_trigger": bool(g.get("at_trigger", 1)),
            })
        return _json(result)

    async def _api_list_nicknames(self):
        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str) and "," in m]
        result = {}
        for m in mappings:
            uid, _, name = m.partition(",")
            result[uid.strip()] = name.strip()
        return _json(result)

    async def _api_set_nickname(self):
        payload = (await request.get_json()) or {}
        user_id = payload.get("user_id", "").strip()
        nickname = payload.get("nickname", "").strip()
        if not user_id or not nickname:
            return _err("缺失 user_id 或 nickname", status_code=400)
        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str)]
        entry = f"{user_id},{nickname}"
        existing = [i for i, m in enumerate(mappings) if m.startswith(user_id + ",")]
        if existing:
            mappings[existing[0]] = entry
        else:
            mappings.append(entry)
        self.config["nickname_mappings"] = mappings
        self.config.save_config()
        return _json({"user_id": user_id, "nickname": nickname})

    async def _api_delete_nickname(self):
        payload = (await request.get_json()) or {}
        user_id = payload.get("user_id", "").strip()
        if not user_id:
            return _err("缺失 user_id", status_code=400)
        mappings = [m for m in (self.config.get("nickname_mappings") or []) if isinstance(m, str)]
        new_mappings = [m for m in mappings if not m.startswith(user_id + ",")]
        if len(new_mappings) == len(mappings):
            return _err("not found", status_code=404)
        self.config["nickname_mappings"] = new_mappings
        self.config.save_config()
        return _json({"deleted": user_id})
