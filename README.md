# astrbot_plugin_crowd_persona_distiller

群友蒸馏bot — 自动记录 QQ 群聊消息，用 LLM 蒸馏群友人格，随机扮演群友语气回复。

Inspired by [pig-skill](https://github.com/Neko-Suwako/pig-skill).

> [!NOTE]
> 本插件基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 开发。
>
> [AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个适合个人和群组对话的智能助手，可以部署在 QQ、Telegram、飞书、钉钉、Slack、LINE、Discord 等数十个主流即时消息平台上。

## 功能

- **消息记录**：自动记录指定群聊的所有消息（排除 Bot 自身和系统消息），使用时间戳去重防止重复导入
- **人格蒸馏**：用 LLM 分析群友聊天记录，生成五层性格画像（Persona.md）
- **扮演回复**：概率触发或冷群检测时，随机选一个群友，用其语气在群里说话
- **@Bot 触发**：群友 @机器人时可以触发扮演回复
- **自动改群名**：扮演时将群名改为该群友昵称（可配置关闭）
- **WebUI 管理**：群友列表、群配置、Persona 编辑器、数据导入

## 使用流程

```
┌─────────────────────────────────────────────────────────────────┐
│  ① 插件启用 → 自动开始记录群聊消息                               │
│     无需任何配置，进入 target_groups 的群后自动录音               │
├─────────────────────────────────────────────────────────────────┤
│  ② 导入历史数据（可选）                                          │
│     WebUI → 数据导入 → 上传 qq-chat-exporter 导出的 JSON         │
│     支持分片上传大文件，导入时自动去重                             │
├─────────────────────────────────────────────────────────────────┤
│  ③ 蒸馏人格                                                      │
│     WebUI → 群友列表 → 点击"蒸馏"按钮                            │
│     需先配置 distill_provider（LLM 提供商）                      │
│     消息数需 ≥ min_distill_messages（默认 50 条）                 │
├─────────────────────────────────────────────────────────────────┤
│  ④ 配置回复                                                      │
│     WebUI → 群配置 → 选择群 → 设置扮演模式                       │
│     - 随机群友：从已蒸馏群友中随机选择                            │
│     - 指定群友：固定使用某个群友的人格                            │
│     - 关闭扮演：该群不进行扮演回复                                │
│     需先配置 reply_provider                                      │
├─────────────────────────────────────────────────────────────────┤
│  ⑤ Bot 自动扮演回复                                              │
│     触发方式：@Bot / 概率随机 / 冷群检测                         │
│     回复时会将 persona.md + 最近聊天记录提交给 LLM                │
│     若该群尚未蒸馏任何群友，Bot 会提示先去 Web 面板蒸馏           │
└─────────────────────────────────────────────────────────────────┘
```

## 安装

```bash
# 克隆到 AstrBot 插件目录
cd AstrBot/data/plugins
git clone https://github.com/AisuKyobu/astrbot_plugin_crowd_persona_distiller

# 安装依赖
pip install -r astrbot_plugin_crowd_persona_distiller/requirements.txt
```

在 AstrBot WebUI 中启用插件，配置蒸馏和回复用的 LLM 提供商后即可使用。

## 数据导入

插件支持导入 [qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter) 导出的 JSON 格式聊天记录。

### 导出步骤

1. 从 [Releases](https://github.com/shuakami/qq-chat-exporter/releases) 下载 qq-chat-exporter
2. 运行并扫码登录，复制 Token
3. 打开 `http://localhost:40653/qce-v4-tool`，选择目标群
4. 导出格式选择 **JSON**，点击导出
5. 得到文件如 `group_群名_822355274_20260621_022728.json`
6. 在插件 WebUI → 数据导入 → 上传该 JSON 文件

### JSON 格式要求

导出文件需包含以下字段（qq-chat-exporter v5.x 默认输出即符合）：

```json
{
  "chatInfo": {"name": "群名", "type": "group"},
  "messages": [
    {
      "timestamp": 1754820721000,
      "sender": {"uin": "2689449524", "name": "家乐摩西"},
      "content": {"text": "消息正文"},
      "system": false,
      "recalled": false
    }
  ]
}
```

**导入时插件会自动识别：群名、所有发言用户、消息数，并排除系统消息和 图片/视频/卡片/合并转发 等非文本消息。重复导入同一文件不会产生重复数据。**

## 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `distill_provider` | 蒸馏分析用 LLM 提供商 | — |
| `reply_provider` | 回复生成用 LLM 提供商 | — |
| `target_groups` | 监听的群号列表（空=全部） | `[]` |
| `min_distill_messages` | 蒸馏最少消息数 | 50 |
| `context_message_count` | 回复时携带的上下文消息数 | 20 |
| `reply_probability` | 回复触发概率 | 0.05 |
| `reply_cooldown_minutes` | 同群回复冷却（分钟） | 120 |
| `cold_group_hours` | 冷群检测阈值（小时） | 4 |
| `enable_name_change` | 扮演时改群名 | true |

## WebUI 面板

| Tab | 功能 |
|-----|------|
| 群友列表 | 查看已蒸馏/待蒸馏/消息不足的群友，按群筛选，触发蒸馏 |
| 群配置 | 按群设置扮演模式（随机/指定/关闭）、@触发开关 |
| Persona 编辑器 | 加载、编辑、保存群友的 persona.md |
| 数据导入 | 上传 qq-chat-exporter JSON，预览并导入聊天记录 |

## 去重机制

采用 `UNIQUE(group_id, user_id, timestamp, content)` 联合唯一键，`INSERT OR IGNORE` 写入。

- 同一人同一秒说相同内容 → 跳过（真重复）
- 同一人说"好的"在不同时间 → 全部保留
- 实时消息与导入消息使用同一时间源，确保全局去重

## 项目结构

```
astrbot_plugin_crowd_persona_distiller/
├── main.py              # 插件入口、消息监听、WebAPI
├── storage.py           # SQLite 数据层
├── persona_mgr.py       # 人格蒸馏与管理
├── reply_engine.py      # 扮演回复引擎
├── chat_parser.py       # QQ 聊天记录解析
├── _conf_schema.json    # 配置 schema
├── prompts/             # LLM prompt 模板
│   ├── persona_analyzer.md
│   └── persona_builder.md
└── pages/               # WebUI 前端
    └── pig-manager/
        ├── index.html
        ├── style.css
        └── app.js
```

## Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
