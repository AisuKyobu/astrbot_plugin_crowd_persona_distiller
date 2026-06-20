# Spec: 群友蒸馏bot (astrbot_plugin_crowd_persona_distiller)

## Objective

将 QQ 群聊中的群友"蒸馏"为可扮演的 AI Persona。Bot 自动记录群聊消息，用 LLM 分析生成群友人格画像，然后随机扮演群友回复消息或修改群名。灵感来自 [pig-skill](https://github.com/Neko-Suwako/pig-skill)。

**目标用户：** 使用 QQ 个人号（OneBot v11/aiocqhttp）的 AstrBot 用户。

**成功标准：**
- Bot 能自动记录指定群的消息（排除自身和系统消息）
- 用户可通过 WebUI 触发蒸馏，将某群友的历史消息分析为 persona.md
- Bot 以概率或冷群检测触发，扮演随机群友在群里说话
- 扮演时将群名改为该群友昵称（可配置关闭/黑名单）
- WebUI 提供群友管理面板（列表、编辑 persona、上传导入）

## Tech Stack

- **语言：** Python 3.10+
- **框架：** AstrBot (v4.x) 插件系统
- **存储：** aiosqlite（消息记录 + 群状态）
- **LLM：** 通过 AstrBot 的 `context.llm_generate()` 调用，支持分别配置蒸馏/回复模型
- **消息平台：** aiocqhttp (OneBot V11)
- **前端：** AstrBot 插件 Pages（HTML + JS + bridge API）

## Commands

```
# 部署环境
cd AstrBot/data/plugins
git clone <plugin_repo_url>
cd ../../..

# AstrBot 启动（插件自动加载）
python main.py

# WebUI 管理
打开 http://localhost:6185 → 插件 → 群友
```

## Project Structure

```
astrbot_plugin_crowd_persona_distiller/
├── metadata.yaml              # 插件元数据
├── _conf_schema.json          # WebUI 配置表单
├── main.py                    # 入口：事件监听 + 指令 + WebAPI
├── storage.py                 # SQLite 存储层
├── persona_mgr.py             # Persona CRUD + 蒸馏
├── reply_engine.py            # 回复引擎 + 改名
├── chat_parser.py             # qq-chat-exporter 格式解析
├── requirements.txt           # aiosqlite, aiohttp
├── logo.png
├── prompts/
│   ├── persona_analyzer.md    # 性格分析 Prompt
│   └── persona_builder.md     # Persona 生成模板
└── pages/
    └── pig-manager/
        ├── index.html
        ├── app.js
        └── style.css

# 运行时数据（AstrBot data 目录下）
data/plugin_data/astrbot_plugin_crowd_persona_distiller/
├── pig.db                     # SQLite
└── pigs/{slug}/
    ├── persona.md
    ├── meta.json
    └── versions/
```

## Code Style

遵循 AstrBot 插件规范：
- 插件类继承 `Star`，使用 `@register` 装饰器
- 消息监听用 `@filter.event_message_type(EventMessageType.GROUP_MESSAGE)`
- 指令用 `@filter.command` / `@filter.command_group`
- WebAPI 用 `context.register_web_api()`
- 配置通过 `_conf_schema.json` + `config` 参数注入
- 异步优先：所有 I/O 用 async/await
- 使用 `astrbot.api.web` 的 `request`, `json_response`, `error_response`
- 日志用 `from astrbot.api import logger`

## Testing Strategy

- **单元测试：** `storage.py` 的 SQLite CRUD 操作
- **集成测试：** 蒸馏流程（消息 → LLM 分析 → persona 生成）
- **端到端：** 在 AstrBot 中加载插件，发送消息验证记录和回复
- **验证命令：** `python -c "from astrbot_plugin_crowd_persona_distiller.storage import PigStorage; ..."`

## Boundaries

### Always do:
- 排除 Bot 自身消息（`event.get_self_id()`）
- 过滤系统消息（入群/退群/撤回）和纯数字/单字
- 数据存于 `data/plugin_data/`，不存插件目录
- 蒸馏前检查消息数 >= 最小阈值
- 回复前检查冷却时间
- 冷群检测排除夜间(2:00-8:00)

### Ask first:
- 改群名功能是否启用（用户通过配置/WebUI 控制）
- 使用哪个 LLM 模型做蒸馏/回复

### Never do:
- 记录 Bot 自身发出的消息
- 扮演群友时暴露 Bot 身份（不提及 AI/Bot）
- 未经配置修改群名
- 在非指定群中记录消息

## Success Criteria

- [ ] 消息自动记录到 SQLite，过滤规则全部生效
- [ ] 蒸馏：触发后 LLM 生成 persona.md，格式符合 pig-skill 五层结构
- [ ] 扮演回复：概率触发（默认 5%）和冷群（4h 无人）均生效
- [ ] 群名：扮演时自动改为 persona 昵称，受冷却和黑名单约束
- [ ] WebUI：群友列表、蒸馏按钮、persona 编辑器、数据导入 全部可用
- [ ] CLI 指令：`/pig list|distill|reply|delete` 正常响应

## Open Questions

- [ ] `llm_generate()` 是否支持 `system_prompt` 参数？（需验证，可能需用 `contexts` 传 system 消息）
- [ ] aiocqhttp 的 `set_group_name` API 是否可用？调用方式？
- [ ] WebUI 上传的大文件是否需要分片？qq-chat-exporter JSON 可能上百 MB
