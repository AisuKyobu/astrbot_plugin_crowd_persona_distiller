"""
QQ 聊天记录导出解析器

适配 https://github.com/shuakami/qq-chat-exporter 导出的 JSON 格式。

核心思路：
1. 兼容多种 JSON 结构（list / dict with messages/records/data fields）
2. 提取发送者、消息内容、时间戳
3. 支持按目标人物筛选
4. 过滤系统消息、空消息、文件/图片占位符
"""

from datetime import datetime


def parse_qq_export_json(data, target_name: str = "") -> list[dict]:
    """
    解析 qq-chat-exporter 导出的 JSON 文件

    Args:
        data: JSON 数据（可以是列表或字典）
        target_name: 目标人物昵称，空则提取所有人

    Returns:
        list[dict]: 消息列表，每条为 {"sender": str, "content": str, "timestamp": str}
    """
    raw_messages = _extract_raw_messages(data)
    messages = []

    for msg in raw_messages:
        sender = _extract_sender(msg)
        content = _extract_content(msg)
        timestamp = _extract_timestamp(msg)

        if not content or not sender:
            continue

        if _is_system_or_media(content):
            continue

        if target_name and target_name not in str(sender):
            continue

        messages.append(
            {
                "sender": str(sender),
                "content": str(content).strip(),
                "timestamp": str(timestamp),
            }
        )

    return messages


def _extract_raw_messages(data) -> list:
    """兼容多种 JSON 顶层结构"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("messages", "records", "data", "msg", "chatlog", "chatLogs"):
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for sub_key in ("messages", "records", "data", "msg"):
                    sub = val.get(sub_key)
                    if isinstance(sub, list):
                        return sub
    return []


def _extract_sender(msg: dict) -> str:
    """提取发送者名称"""
    if not isinstance(msg, dict):
        return ""

    for key in (
        "sender_name",
        "senderName",
        "sender",
        "from_user",
        "fromUser",
        "user_name",
        "userName",
        "nickname",
        "nick",
        "name",
        "qq_nick",
        "qqNick",
        "userid",
        "user_id",
        "uin",
    ):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, (int, float)):
            return str(int(val))

    sender_obj = msg.get("sender")
    if isinstance(sender_obj, dict):
        for key in ("nickname", "nick", "name", "user_name", "userName"):
            val = sender_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _extract_content(msg: dict) -> str:
    """提取消息正文"""
    if not isinstance(msg, dict):
        return ""

    for key in (
        "content",
        "message",
        "text",
        "msg",
        "body",
        "message_content",
        "raw_message",
        "rawMessage",
    ):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    messages_list = (
        msg.get("messages") or msg.get("message_chain") or msg.get("messageChain")
    )
    if isinstance(messages_list, list):
        texts = []
        for m in messages_list:
            if isinstance(m, dict):
                t = (
                    m.get("text")
                    or m.get("content")
                    or m.get("data", {}).get("text", "")
                )
                if isinstance(t, str):
                    texts.append(t)
        return " ".join(texts)

    return ""


def _extract_timestamp(msg: dict) -> str:
    """提取时间戳，统一转为 ISO 字符串"""
    ts = None
    for key in (
        "timestamp",
        "time",
        "create_time",
        "created_at",
        "msg_time",
        "msgTime",
    ):
        val = msg.get(key)
        if val is not None:
            ts = val
            break

    if ts is None:
        return ""

    if isinstance(ts, str):
        return ts

    if isinstance(ts, (int, float)):
        if ts > 1e12:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts).isoformat()
        except (OSError, ValueError):
            return str(ts)

    return str(ts)


def _is_system_or_media(content: str) -> bool:
    """过滤系统消息和纯媒体占位符"""
    if not content or not content.strip():
        return True

    stripped = content.strip()

    if stripped in (
        "[图片]",
        "[文件]",
        "[视频]",
        "[语音]",
        "[表情]",
        "[动画表情]",
        "[戳一戳]",
    ):
        return True

    if stripped.startswith("[") and stripped.endswith("]") and len(stripped) < 20:
        return True

    system_markers = [
        "撤回了一条消息",
        "加入群聊",
        "退出群聊",
        "被踢出群聊",
        "修改群名称为",
        "管理员已开启全体禁言",
        "管理员已关闭全体禁言",
    ]
    for marker in system_markers:
        if marker in stripped:
            return True

    return False


def extract_import_summary(data) -> dict:
    """
    解析导出文件的摘要信息：群信息 + 所有用户 + 各自消息数

    Returns:
        {
            "group_id": "123456",       # 可能为空
            "group_name": "xxx群",       # 可能为空
            "total_messages": 500,
            "users": [
                {"user_id": "789", "user_name": "小明", "message_count": 150, "sample": "最近一条消息..."},
                ...
            ]
        }
    """
    raw = _extract_raw_messages(data)

    group_id = ""
    group_name = ""
    if isinstance(data, dict):
        group_id = str(
            data.get("group_id") or data.get("groupId") or data.get("groupUin") or ""
        )
        group_name = str(
            data.get("group_name") or data.get("groupName") or data.get("name") or ""
        )

    user_map: dict[str, dict] = {}
    for msg in raw:
        sender_name = _extract_sender(msg)
        sender_id = _extract_sender_id(msg) or sender_name
        content = _extract_content(msg)
        if not content or not sender_name:
            continue
        if _is_system_or_media(content):
            continue

        key = sender_id
        if key not in user_map:
            user_map[key] = {
                "user_id": sender_id,
                "user_name": sender_name,
                "message_count": 0,
                "sample": content,
            }
        user_map[key]["message_count"] += 1
        user_map[key]["sample"] = content

    users = sorted(user_map.values(), key=lambda u: u["message_count"], reverse=True)

    if not group_id and users:
        for msg in raw:
            gid = _extract_group_id(msg)
            if gid:
                group_id = str(gid)
                break

    return {
        "group_id": group_id,
        "group_name": group_name,
        "total_messages": sum(u["message_count"] for u in users),
        "users": users,
    }


def _extract_sender_id(msg: dict) -> str:
    if not isinstance(msg, dict):
        return ""
    for key in (
        "sender_id", "senderId", "user_id", "userId", "uin", "qq",
        "sender_uin", "senderUin",
    ):
        val = msg.get(key)
        if isinstance(val, (str, int, float)) and str(val).strip():
            return str(int(val) if isinstance(val, float) else val).strip()

    sender_obj = msg.get("sender")
    if isinstance(sender_obj, dict):
        for key in ("user_id", "userId", "uin", "qq"):
            val = sender_obj.get(key)
            if isinstance(val, (str, int, float)) and str(val).strip():
                return str(int(val) if isinstance(val, float) else val).strip()
    return ""


def _extract_group_id(msg: dict) -> str:
    if not isinstance(msg, dict):
        return ""
    for key in ("group_id", "groupId", "groupUin", "group_code", "groupCode"):
        val = msg.get(key)
        if isinstance(val, (str, int, float)) and str(val).strip():
            return str(int(val) if isinstance(val, float) else val).strip()
    return ""
