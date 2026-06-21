# Implementation Plan: 群友蒸馏bot

## Overview

构建一个 AstrBot 插件，自动记录群聊 → LLM 蒸馏群友人格 → 概率/冷群触发扮演回复。分 3 个 Phase，共 11 个任务。

## Architecture Decisions

1. **两表 SQLite + 文件 Persona**：消息和群状态走 SQLite（高频写入），persona 走文件（兼容 pig-skill 格式，WebUI 直接编辑）
2. **Persona 注入 system prompt**：扮演时把 persona.md 作为 system_prompt，最近 20 条消息作为上下文(user)
3. **双 LLM 配置**：蒸馏/回复分开选 Provider（蒸馏需要强分析，回复需要快速便宜）
4. **冷群检测用 asyncio.create_task**：后台 60s 轮询，不阻塞消息处理
5. **改名走 aiocqhttp bot API**：`bot.set_group_name(group_id=..., group_name=...)`

## Task List

### Phase 1: Foundation（基础设施）

#### Task 1: 补全 reply_engine.py
**Description:** 实现回复引擎核心逻辑：概率触发判断、冷群检测后台任务、LLM 生成回复、群名修改。

**Acceptance criteria:**
- [ ] `should_reply_probability()` 根据概率随机返回 True/False
- [ ] `should_reply_cold()` 根据 last_message_at + 休眠时段返回 True/False
- [ ] `generate_and_send()` 构建 system_prompt + 上下文 → LLM 生成 → 发送
- [ ] `change_group_name()` 调用 aiocqhttp `set_group_name` API
- [ ] 冷却检查：同群 2h、同人 30min、改名 1h

**Verification:**
- [ ] 代码通过 Python 语法检查
- [ ] 导入路径正确

**Dependencies:** Task 0（已完成的 storage.py + persona_mgr.py）

**Files likely touched:**
- `reply_engine.py`

**Estimated scope:** Medium (3-5 files)

---

#### Task 2: 补全 main.py（骨架 + 事件监听 + 指令）
**Description:** 创建插件类，注册群消息监听器（记录+触发回复），注册 CLI 指令，注册 WebAPI 路由。

**Acceptance criteria:**
- [ ] `@filter.event_message_type(GROUP_MESSAGE)` 监听所有群消息
- [ ] 消息过滤：排除 Bot 自身、纯系统消息、过短消息、Bot 指令（`/` 开头）
- [ ] 每条有效消息写入 SQLite + 更新 group_state
- [ ] 概率触发检查 → 选随机 persona → 调用 reply_engine
- [ ] 指令 `/pig list|distill|reply|delete` 全部可用
- [ ] WebAPI 路由已注册（list/GET, distill/POST, edit/GET+POST, import/POST）

**Verification:**
- [ ] 无 import 错误
- [ ] 指令装饰器语法正确

**Dependencies:** Task 1

**Files likely touched:**
- `main.py`

**Estimated scope:** Medium (3-5 files)

---

#### Task 3: WebUI 群友管理面板
**Description:** 创建 plugin page（`pages/persona-distiller/`），包含群友列表、蒸馏按钮、persona 编辑器、数据导入。

**Acceptance criteria:**
- [ ] 群友列表：显示 slug/昵称/消息数/上次蒸馏时间
- [ ] 蒸馏按钮：点击触发 `/api/v1/plugins/extensions/astrbot_plugin_crowd_persona_distiller/distill` POST
- [ ] Persona 编辑器：选择群友 → code editor 编辑 persona.md → 保存
- [ ] 数据导入：上传 JSON 文件 → 预览 → 确认导入
- [ ] 使用 `window.AstrBotPluginPage` bridge 通信

**Verification:**
- [ ] HTML 文件在 `pages/persona-distiller/index.html`
- [ ] 使用 bridge API（apiGet/apiPost/upload）
- [ ] 亮暗主题 CSS 变量适配

**Dependencies:** Task 2

**Files likely touched:**
- `pages/persona-distiller/index.html`
- `pages/persona-distiller/app.js`
- `pages/persona-distiller/style.css`

**Estimated scope:** Medium (3-5 files)

---

### Checkpoint: Foundation
- [ ] Task 1-3 全部完成
- [ ] 无 import 错误
- [ ] `python -c "import astrbot_plugin_crowd_persona_distiller"` 不报错

---

### Phase 2: Verification（验证修复）

#### Task 4: 端到端验证 + `llm_generate` API 确认
**Description:** 确认 `context.llm_generate()` 的 system_prompt 参数，如不支持则改用 `contexts` 参数。

**Acceptance criteria:**
- [ ] 调用 `llm_generate` 成功返回
- [ ] system prompt 正确注入
- [ ] 蒸馏流程端到端跑通（LLM → 分析 → persona.md 落盘）

**Verification:**
- [ ] 实际调用一次蒸馏，确认 persona.md 生成内容合理

**Dependencies:** Task 2

**Files likely touched:**
- `persona_mgr.py`
- `main.py`

**Estimated scope:** Small (1-2 files)

---

#### Task 5: 改名 API 验证
**Description:** 确认 aiocqhttp 的 `set_group_name` 调用方式，实现改名并捕获可能的错误（权限不足、平台不支持）。

**Acceptance criteria:**
- [ ] aiocqhttp 改名成功（测试群）
- [ ] 权限不足时捕获异常，不崩溃
- [ ] 非 aiocqhttp 平台静默跳过改名

**Verification:**
- [ ] 在测试群中扮演 → 群名确实改变
- [ ] 在黑名单中的人扮演 → 不改群名
- [ ] 冷却期内 → 不改群名

**Dependencies:** Task 1, Task 2

**Files likely touched:**
- `reply_engine.py`

**Estimated scope:** Small (1-2 files)

---

### Checkpoint: Verification
- [ ] LLM 调用正常
- [ ] 改名功能正常
- [ ] 端到端流程跑通

---

### Phase 3: Polish（完善 + 边角）

#### Task 6: 消息过滤精细化
**Description:** 完善消息过滤逻辑：系统消息检测、Bot 指令检测（参考 `astr_message_event.py` 的 `is_at_or_wake_command`）、纯表情检测。

**Acceptance criteria:**
- [ ] `event.is_at_or_wake_command == True` 时跳过记录
- [ ] 消息只含 `[图片]`/`[表情]`/`[文件]` 时跳过
- [ ] 消息长度 < 2 跳过
- [ ] 公告/通知类系统消息跳过

**Verification:**
- [ ] 发送不同类型消息，确认过滤正确

**Dependencies:** Task 2

**Files likely touched:**
- `main.py`

**Estimated scope:** Small (1-2 files)

---

#### Task 7: lints + 错误处理完善
**Description:** 添加 try/except 保护关键路径，添加日志，确保插件不因单次错误崩溃。

**Acceptance criteria:**
- [ ] LLM 调用失败时有日志 + 不回崩溃
- [ ] 改名失败时有日志 + 不崩溃
- [ ] 数据库操作失败时有日志
- [ ] 所有异步操作有异常保护

**Verification:**
- [ ] 故意断开 LLM 连接 → 插件不崩溃

**Dependencies:** Task 4

**Files likely touched:**
- `persona_mgr.py`
- `reply_engine.py`
- `main.py`

**Estimated scope:** Small (2-3 files)

---

### Checkpoint: Complete
- [ ] 所有 acceptance criteria 通过
- [ ] 可交付 MVP

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `llm_generate` 不支持 system_prompt | High | 改用 `contexts=[SystemMessage(...), UserMessage(...)]` |
| aiocqhttp 改名 API 不可用 | Medium | 降级为仅文本日志，不改名 |
| WebUI 上传大 JSON 超时 | Low | 前端分片上传 or 限制文件大小 |
| 蒸馏消耗大量 Token 超预算 | Medium | 限制分析消息上限 500 条，提示用户用便宜模型 |
| 群聊消息量巨大导致 SQLite 膨胀 | Low | 设置消息保留天数（P2），默认 90 天 |

## Open Questions

1. `context.llm_generate()` 接受 `system_prompt` 参数吗？
2. aiocqhttp 的 `bot.set_group_name()` 调用格式？
3. 蒸馏时一次性提交 500 条消息是否会超出 token 限制？
