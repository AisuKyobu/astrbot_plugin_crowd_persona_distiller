# astrbot_plugin_crowd_persona_distiller

群友蒸馏bot — 自动记录 QQ 群聊消息，用 LLM 蒸馏群友人格，随机扮演群友语气回复或修改群名称。

Inspired by [pig-skill](https://github.com/Neko-Suwako/pig-skill).

> [!NOTE]
> 本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发。
>
> [AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个适合个人和群组对话的智能助手，可以部署在 QQ、Telegram、飞书、钉钉、Slack、LINE、Discord 等数十个主流即时消息平台上。

## 功能

- **消息记录**：自动记录指定群聊的所有消息（排除 Bot 自身和系统消息）
- **人格蒸馏**：用 LLM 分析群友聊天记录，生成五层性格画像（Persona）
- **扮演回复**：概率触发或冷群检测时，随机选一个群友，用其语气在群里说话
- **自动改群名**：扮演时将群名改为该群友昵称（可配置关闭）
- **WebUI 管理**：群友列表、Persona 编辑器、数据导入（支持 qq-chat-exporter 格式）

## 指令

| 指令 | 说明 |
|------|------|
| `/qunyou list` | 列出所有已蒸馏群友 |
| `/qunyou distill <名字>` | 蒸馏指定群友 |
| `/qunyou reply <name>` | 立刻用某群友语气回复 |
| `/qunyou delete <name>` | 删除某个群友 Persona |

## 安装

```bash
# 克隆到 AstrBot 插件目录
cd AstrBot/data/plugins
git clone https://github.com/AisuKyobu/astrbot_plugin_crowd_persona_distiller

# 安装依赖
pip install -r astrbot_plugin_crowd_persona_distiller/requirements.txt
```

在 AstrBot WebUI 中启用插件，配置蒸馏和回复用的 LLM 提供商后即可使用。

## 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `distill_provider` | 蒸馏分析用 LLM | — |
| `reply_provider` | 回复生成用 LLM | — |
| `reply_probability` | 回复触发概率 | 0.05 |
| `reply_cooldown_minutes` | 同群回复冷却（分钟） | 120 |
| `cold_group_hours` | 冷群判定（小时） | 4 |
| `enable_name_change` | 扮演时改群名 | true |
| `min_distill_messages` | 蒸馏最少消息数 | 50 |

# Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
