"""
QQ 聊天记录导出解析器

适配 https://github.com/shuakami/qq-chat-exporter v5.x 导出的 JSON 格式。

JSON 结构:
{
  "chatInfo": {"name": "群名", "type": "group", "selfUin": "123456"},
  "messages": [
    {
      "timestamp": 1754820721000,          // 毫秒
      "sender": {"uin": "2689449524", "name": "家乐摩西", ...},
      "content": {"text": "消息正文", "elements": [...]},
      "type": "type_1",
      "system": false,
      "recalled": false
    }
  ]
}
"""

from datetime import datetime


def _get_messages(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        msgs = data.get("messages")
        if isinstance(msgs, list):
            return msgs
    return []


def _get_sender_name(msg: dict) -> str:
    sender = msg.get("sender")
    if isinstance(sender, dict):
        name = sender.get("name") or sender.get("groupCard") or sender.get("nickname") or ""
        return str(name).strip()
    return ""


def _get_sender_uin(msg: dict) -> str:
    sender = msg.get("sender")
    if isinstance(sender, dict):
        uin = sender.get("uin") or sender.get("uid") or ""
        return str(uin).strip()
    return ""


def _get_content_text(msg: dict) -> str:
    content = msg.get("content")
    if not isinstance(content, dict):
        return ""
    text = content.get("text", "")
    if isinstance(text, str):
        return text.strip()
    elements = content.get("elements")
    if isinstance(elements, list):
        parts = []
        for el in elements:
            if isinstance(el, dict):
                data = el.get("data")
                if isinstance(data, dict):
                    t = data.get("text", "")
                    if t:
                        parts.append(t)
        return "".join(parts).strip()
    return ""


def _is_system_or_media(msg: dict, text: str) -> bool:
    if msg.get("system") or msg.get("recalled"):
        return True
    if not text:
        return True
    if text.startswith("[图片:") or text.startswith("[视频:"):
        return True
    if text.startswith("[卡片消息:"):
        return True
    if text.startswith("[合并转发:"):
        return True
    if text.startswith("[文件:") or text.startswith("[语音:"):
        return True
    if text in ("[图片]", "[文件]", "[视频]", "[语音]", "[表情]", "[动画表情]"):
        return True
    return False


def _format_timestamp(ts) -> str:
    if isinstance(ts, str):
        return ts
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            ts = ts / 1000
        try:
            return datetime.fromtimestamp(ts).isoformat()
        except (OSError, ValueError):
            return str(ts)
    return ""


def parse_qq_export_json(data, target_name: str = "") -> list[dict]:
    """
    解析导出 JSON，提取消息列表。

    Args:
        data: JSON 数据
        target_name: 目标昵称或 QQ 号，空则提取所有人

    Returns:
        [{"sender": str, "content": str, "timestamp": str}, ...]
    """
    raw = _get_messages(data)
    messages = []
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        sender_name = _get_sender_name(msg)
        sender_uin = _get_sender_uin(msg)
        content = _get_content_text(msg)

        if _is_system_or_media(msg, content):
            continue

        if target_name and target_name not in sender_name and target_name not in sender_uin:
            continue

        messages.append({
            "sender": sender_name,
            "sender_uin": sender_uin,
            "content": content,
            "timestamp": _format_timestamp(msg.get("timestamp")),
        })
    return messages


def extract_import_summary(data, filename: str = "") -> dict:
    """
    解析导出文件摘要：群信息 + 所有用户 + 各自消息数。

    Returns:
        {
            "group_id": "822355274",
            "group_name": "🍭🐔4⃣️🐴🐴",
            "total_messages": 500,
            "users": [
                {"user_id": "2689449524", "user_name": "家乐摩西", "message_count": 150, "sample": "最近一条消息..."},
                ...
            ]
        }
    """
    raw = _get_messages(data)

    chat_info = data.get("chatInfo") if isinstance(data, dict) else {}
    group_name = str(chat_info.get("name", ""))
    self_uin = str(chat_info.get("selfUin", "") or chat_info.get("selfUid", ""))

    group_id = ""
    if filename:
        import re
        match = re.search(r"group_.*?_(\d+)_", filename)
        if match:
            group_id = match.group(1)

    user_map: dict[str, dict] = {}
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        sender_name = _get_sender_name(msg)
        sender_uin = _get_sender_uin(msg)
        content = _get_content_text(msg)

        if _is_system_or_media(msg, content):
            continue
        if sender_uin == self_uin:
            continue

        key = sender_uin or sender_name
        if key not in user_map:
            user_map[key] = {
                "user_id": sender_uin or sender_name,
                "user_name": sender_name,
                "message_count": 0,
                "sample": content,
            }
        user_map[key]["message_count"] += 1
        user_map[key]["sample"] = content

    users = sorted(user_map.values(), key=lambda u: u["message_count"], reverse=True)

    return {
        "group_id": group_id,
        "group_name": group_name,
        "total_messages": sum(u["message_count"] for u in users),
        "users": users,
    }
