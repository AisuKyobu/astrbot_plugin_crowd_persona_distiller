# AGENTS.md

AstrBot plugin: 群友蒸馏bot — records QQ group chat, distills personas via LLM, impersonates group members.
Deployed as `AstrBot/data/plugins/astrbot_plugin_crowd_persona_distiller/`.

## Commands

```
py_compile: python -c "import py_compile; py_compile.compile(r'<file>', doraise=True)"
```

No test suite. Verify by deploying into AstrBot and reloading plugin.

## Architecture

```
main.py           Plugin entry, message listener, CLI commands, WebAPI routes
storage.py        SQLite (aiosqlite): messages, group_state, personas index
persona_mgr.py    Persona CRUD + LLM distillation + chat-export import
reply_engine.py   Probability/cold-group/at-trigger reply + group name change
chat_parser.py    qq-chat-exporter v5.x JSON format parser
_conf_schema.json Plugin config schema (select_provider, probabilities, etc.)
```

## Critical gotchas (hard-won)

### 1. Use Quart, NOT astrbot.api.web

`astrbot.api.web` does not exist in many AstrBot versions. Always use:

```python
from quart import jsonify, request

def _json(data, status_code=200):
    return jsonify(data), status_code

def _err(msg, status_code=400):
    return jsonify({"status": "error", "message": msg}), status_code
```

### 2. Register WebAPI routes with AND without plugin prefix

AstrBot Dashboard route matching uses `route == f"/{subpath}"` where `subpath` depends on version.
Register both:

```python
for route_prefix in (f"/{p}", ""):
    self.context.register_web_api(f"{route_prefix}/endpoint", handler, [...])
```

### 3. HAVING alias + parameter = 0 rows in Docker SQLite

```sql
-- BROKEN (returns 0 rows):
HAVING message_count >= ?

-- FIXED:
HAVING COUNT(*) >= ?
```

### 4. File upload: chunked base64 via apiPost

`bridge.upload()` and large `apiPost` bodies both fail. Use 500KB chunks:

```
Frontend: FileReader → base64 → 500KB chunks → N × apiPost("import/chunk")
          → apiPost("import/assemble") triggers server-side assembly
Backend:  chunks → temp file append → assemble → base64 decode → parse JSON
```

### 5. DB migrations: ALTER TABLE with try/except

New columns on `group_state` table — `IF NOT EXISTS` doesn't work for ALTER:

```python
for col, default in (("group_name", "''"), ("reply_mode", "'random'"), ...):
    try:
        await self._conn.execute(f"ALTER TABLE group_state ADD COLUMN {col} TEXT DEFAULT {default}")
    except Exception:
        pass
```

### 6. Slug uniqueness across groups

Same user in different groups must have different slugs:

```python
slug = slugify(f"{user_name}_{group_id}")  # NOT just slugify(user_name)
```

### 7. Data persistence

- SQLite: `data/plugin_data/astrbot_plugin_crowd_persona_distiller/pig.db`
- Persona files: `pigs/{slug}/persona.md` + `meta.json`
- Docker: mount `/AstrBot/data` to host, or data lost on container recreate

### 8. Message filtering order (matters)

```python
if event.get_sender_id() == event.get_self_id(): return
if event.is_at_or_wake_command:    # → @Bot trigger, do NOT record
    await self.reply_engine.do_reply(...); return
if message_type is OTHER_MESSAGE: return
if not content or len(content) < 2: return
if content.startswith("/"): return    # bot commands
# ... record message + probability trigger
```

### 9. Docker deployment flow

Container tracks `origin/master`. Update: `docker exec astrbot git -C ... pull` → WebUI reload plugin.
Always push both `main` and `master` branches.

### 10. `_conf_schema.json` requires `"type"` on every field

Even `_special` fields need `"type": "string"`. Missing `type` → `KeyError: 'type'` on plugin load.

## Key endpoints (for debugging)

| Endpoint | Description |
|----------|-------------|
| `GET /personas` | Already-distilled persona list |
| `GET /distillable` | All users with message counts and distill status |
| `GET /group_config/<id>` | Per-group reply config |
| `GET /group_configs` | All groups with configs |
| `POST /distill` | Trigger distillation `{group_id, user_id, user_name}` |
| `POST /import/chunk` | Chunked file upload |
| `POST /import/assemble` | Trigger chunk assembly and parsing |
