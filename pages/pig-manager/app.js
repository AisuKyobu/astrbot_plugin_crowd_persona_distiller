const bridge = window.AstrBotPluginPage;

const tabs = document.querySelectorAll(".tab");
const contents = document.querySelectorAll(".tab-content");
const statusText = document.getElementById("status-text");
const personaList = document.getElementById("persona-list");
const personaSelect = document.getElementById("persona-select");
const personaEditor = document.getElementById("persona-editor");
const editorStatus = document.getElementById("editor-status");

let personas = [];
let importToken = "";
let nicknameMap = {};

// ---- Init ----

await bridge.ready();

tabs.forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
);

document.getElementById("btn-refresh").addEventListener("click", loadPersonas);
document.getElementById("btn-load-persona").addEventListener("click", loadPersonaForEdit);
document.getElementById("btn-save-persona").addEventListener("click", savePersona);
document.getElementById("btn-import-preview").addEventListener("click", previewImport);
document.getElementById("btn-import-confirm").addEventListener("click", confirmImport);
document.getElementById("group-filter").addEventListener("change", loadPersonas);
document.getElementById("config-group-select").addEventListener("change", onConfigGroupChange);
document.querySelectorAll("input[name='reply_mode']").forEach((r) => r.addEventListener("change", onModeChange));
document.getElementById("btn-save-config").addEventListener("click", saveConfig);

loadPersonas();

// ---- Tab Switching ----

function switchTab(name) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    contents.forEach((c) => c.classList.toggle("active", c.id === `tab-${name}`));
    if (name === "editor") refreshPersonaSelect();
    if (name === "config") loadGroupConfigs();
}

// ---- Persona List ----

async function loadPersonas() {
    statusText.textContent = "加载中...";
    try {
        const [distilled, allUsers, nicks] = await Promise.all([
            bridge.apiGet("personas"),
            bridge.apiGet("distillable"),
            bridge.apiGet("nicknames"),
        ]);
        nicknameMap = nicks || {};
        personas = distilled;
        const distilledSlugs = new Set(distilled.map((p) => p.slug));

        const pending = allUsers
            .filter((u) => !distilledSlugs.has(u.slug) && u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);

        const notReady = allUsers
            .filter((u) => !distilledSlugs.has(u.slug) && !u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);

        // 构建群筛选下拉
        const groups = new Map();
        for (const u of [...distilled, ...pending, ...notReady]) {
            const gid = String(u.group_id);
            if (!groups.has(gid)) {
                groups.set(gid, u.group_name || gid);
            }
        }
        const filter = document.getElementById("group-filter");
        const currentVal = filter.value;
        filter.innerHTML = '<option value="">全部群</option>';
        for (const [gid, gname] of groups) {
            filter.innerHTML += `<option value="${esc(gid)}">${esc(gname || gid)}</option>`;
        }
        filter.value = currentVal;

        renderPersonaList(distilled, pending, notReady);
        const total = distilled.length + pending.length + notReady.length;
        statusText.textContent = `${distilled.length} 已蒸馏 / ${pending.length} 待蒸馏 / 共 ${total} 人`;
    } catch (e) {
        statusText.textContent = `加载失败: ${e.message}`;
        personaList.innerHTML = '<div class="empty">加载失败，请检查后端连接</div>';
    }
}

function renderPersonaList(distilled, pending, notReady) {
    const filter = document.getElementById("group-filter").value;
    const f = (u) => !filter || String(u.group_id) === filter;
    const d = distilled.filter(f);
    const p = pending.filter(f);
    const n = notReady.filter(f);
    const html = [];

    if (d.length) {
        html.push(`<div class="section-title">已蒸馏群友 <span class="section-count">${d.length}</span></div>`);
        html.push(...d.map((p) => personaCard(p, true)));
    }

    if (p.length) {
        html.push(`<div class="section-title">待蒸馏群友（已达到最少消息数） <span class="section-count">${p.length}</span></div>`);
        html.push(...p.map((u) => distillableCard(u)));
    }

    if (n.length) {
        html.push(`<div class="section-title">消息不足（尚无法蒸馏） <span class="section-count">${n.length}</span></div>`);
        html.push(...n.map((u) => distillableCard(u)));
    }

    if (!html.length) {
        personaList.innerHTML = '<div class="empty">还没有任何群聊消息。在群聊中发言或上传聊天记录导入数据。</div>';
        return;
    }

    personaList.innerHTML = html.join("");

    document.querySelectorAll(".btn-distill").forEach((btn) => {
        btn.addEventListener("click", () =>
            doDistill(btn.dataset.group, btn.dataset.user, btn.dataset.name, btn)
        );
    });

    document.querySelectorAll(".btn-nick-edit").forEach((btn) => {
        btn.addEventListener("click", () =>
            editNickname(btn.dataset.uid, btn.dataset.name)
        );
    });
}

function personaCard(p, isDistilled) {
    const ts = (p.updated_at || p.last_distill_at || "").slice(0, 10);
    const gname = p.group_name ? `${esc(p.group_name)} (${esc(p.group_id)})` : `群 ${esc(p.group_id)}`;
    const uid = esc(p.user_id);
    const nick = nicknameMap[uid] || "";
    const displayName = nick ? `${esc(p.name)} <span class="nickname-badge" title="称呼: ${esc(nick)}" data-uid="${uid}">${esc(nick)}</span>` : esc(p.name);
    const nickAction = `<button class="btn btn-small btn-nick-edit" data-uid="${uid}" data-name="${esc(p.name)}" title="编辑称呼">✎</button>`;
    return `
    <div class="card${isDistilled ? " distilled" : ""}">
      <div class="card-info">
        <div class="card-name">${displayName} <span class="slug-tag">[${esc(p.slug)}]</span></div>
        <div class="card-meta">
          ${gname} · ${p.message_count || 0} 条 · ${ts || "—"}
        </div>
      </div>
      <div class="card-actions">
        ${isDistilled
            ? `<span class="distilled-badge">&#10003; 已蒸馏</span><button class="btn btn-primary btn-distill" data-group="${esc(p.group_id)}" data-user="${uid}" data-name="${esc(p.name)}">重新蒸馏</button>`
            : ""}
        ${nickAction}
      </div>
    </div>`;
}

function distillableCard(u) {
    const lastTs = u.last_msg_at ? new Date(u.last_msg_at * 1000).toLocaleDateString("zh-CN") : "—";
    const gname = u.group_name ? `${esc(u.group_name)} (${esc(u.group_id)})` : `群 ${esc(u.group_id)}`;
    const uid = esc(u.user_id);
    const uname = u.user_name === u.user_id ? `${esc(u.user_name)} (QQ)` : esc(u.user_name);
    const displayUname = uid === esc(u.user_name) ? esc(u.user_name) : uname;
    const nick = nicknameMap[uid] || "";
    const displayName = nick
        ? `${displayUname} <span class="nickname-badge" title="称呼: ${esc(nick)}" data-uid="${uid}">${esc(nick)}</span>`
        : displayUname;
    const minNeeded = 50;
    const bar = Math.min(100, Math.round((u.message_count / minNeeded) * 100));
    const nickAction = `<button class="btn btn-small btn-nick-edit" data-uid="${uid}" data-name="${esc(u.user_name)}" title="编辑称呼">✎</button>`;
    return `
    <div class="card">
      <div class="card-info">
        <div class="card-name">${displayName} <span class="user-id-tag">${uid}</span></div>
        <div class="card-meta">
          ${gname} · ${u.message_count} 条 · 最后发言 ${lastTs}
          ${u.reached_threshold ? "" : `<span class="progress-bar"><span class="progress-fill" style="width:${bar}%"></span><span class="progress-text">${bar}%</span></span>`}
        </div>
      </div>
      <div class="card-actions">
        ${u.reached_threshold
            ? `<button class="btn btn-primary btn-distill" data-group="${esc(u.group_id)}" data-user="${uid}" data-name="${esc(u.user_name)}">蒸馏</button>`
            : `<button class="btn" disabled>需 ${minNeeded} 条</button>`}
        ${nickAction}
      </div>
    </div>`;
}

let _distilling = false;

async function doDistill(groupId, userId, userName, btn) {
    if (_distilling) return;
    _distilling = true;
    const allButtons = document.querySelectorAll(".btn-distill");
    allButtons.forEach((b) => { b.disabled = true; b.textContent = "蒸馏中..."; });
    if (btn) btn.textContent = "处理中...";

    showDistillBanner(`<span class="banner-loading">蒸馏中...</span> 正在分析 ${esc(userName)} 的聊天记录`);
    try {
        const result = await bridge.apiPost("distill", {
            group_id: groupId,
            user_id: userId,
            user_name: userName,
        });
        showDistillBanner(`蒸馏完成！群友 [${esc(result.slug)}] ${esc(result.name)} 已生成`, true);
        loadPersonas();
    } catch (e) {
        showDistillBanner(`蒸馏失败：${esc(e.message)}`, false);
    } finally {
        _distilling = false;
    }
}

async function editNickname(uid, currentName) {
    const current = nicknameMap[uid] || "";
    const label = currentName || uid;
    const input = prompt(`为 ${label} 设置称呼：${current ? "\n（当前: " + current + "）" : ""}`, current || label);
    if (input === null) return;
    const nick = input.trim();
    try {
        if (nick) {
            await bridge.apiPost("nickname", { user_id: uid, nickname: nick });
        } else {
            await bridge.apiPost("nickname/delete", { user_id: uid });
        }
        loadPersonas();
    } catch (e) {
        alert("保存失败: " + e.message);
    }
}

function showDistillBanner(msg, success = null) {
    const existing = document.getElementById("distill-banner");
    if (existing) existing.remove();
    const div = document.createElement("div");
    div.id = "distill-banner";
    div.className = success === true ? "banner banner-ok" : success === false ? "banner banner-err" : "banner banner-info";
    div.innerHTML = msg;
    const list = document.getElementById("persona-list");
    list.insertBefore(div, list.firstChild);
    if (success !== null && success !== false) setTimeout(() => div.remove(), 5000);
}

// ---- Persona Editor ----

function refreshPersonaSelect() {
    personaSelect.innerHTML = '<option value="">-- 选择群友 --</option>';
    personas.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.slug;
        opt.textContent = `[${p.slug}] ${p.name}`;
        personaSelect.appendChild(opt);
    });
}

async function loadPersonaForEdit() {
    const slug = personaSelect.value;
    if (!slug) return;

    editorStatus.textContent = "加载中...";
    try {
        const data = await bridge.apiGet(`persona/${slug}`);
        personaEditor.value = data.content || "";
        editorStatus.textContent = `已加载 [${slug}]`;
    } catch (e) {
        editorStatus.textContent = `加载失败: ${e.message}`;
    }
}

async function savePersona() {
    const slug = personaSelect.value;
    if (!slug) {
        editorStatus.textContent = "请先选择群友";
        return;
    }

    editorStatus.textContent = "保存中...";
    try {
        await bridge.apiPost(`persona/${slug}/save`, {
            content: personaEditor.value,
        });
        editorStatus.textContent = "已保存";
    } catch (e) {
        editorStatus.textContent = `保存失败: ${e.message}`;
    }
}

// ---- Import ----

async function previewImport() {
    const fileInput = document.getElementById("import-file");
    const file = fileInput.files[0];

    if (!file) {
        showImportPreview('<div class="empty">请选择 JSON 文件</div>');
        return;
    }

    showImportPreview('<div class="empty">解析中...</div>');

    try {
        const base64 = await readFileAsBase64(file);
        const CHUNK_SIZE = 500 * 1024; // 500KB
        const totalChunks = Math.ceil(base64.length / CHUNK_SIZE);
        const uploadId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

        showImportPreview(`<div class="empty">上传中 (0/${totalChunks})...</div>`);

        for (let i = 0; i < totalChunks; i++) {
            const chunk = base64.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
            await bridge.apiPost("import/chunk", {
                upload_id: uploadId,
                chunk_index: i,
                total_chunks: totalChunks,
                file_name: file.name,
                data: chunk,
            });
            if (i % 5 === 0 || i === totalChunks - 1) {
                showImportPreview(`<div class="empty">上传中 (${i + 1}/${totalChunks})...</div>`);
            }
        }

        showImportPreview('<div class="empty">解析中...</div>');
        const summary = await bridge.apiPost("import/assemble", {
            upload_id: uploadId,
        });

        importToken = summary.import_token || "";
        const gid = summary.group_id || "";
        const gname = summary.group_name || "";
        const total = summary.total_messages || 0;
        const users = summary.users || [];

        document.getElementById("import-group-id").value = gid;

        let html = `<p><strong>${gname ? esc(gname) + " " : ""}</strong>解析到 <strong>${total}</strong> 条消息，<strong>${users.length}</strong> 个用户</p>`;

        if (users.length > 0) {
            html += '<div class="user-checkboxes">';
            html += '<label class="checkbox-label"><input type="checkbox" id="select-all" checked> 全选</label>';
            users.forEach((u, i) => {
                html += `
                <label class="checkbox-label">
                  <input type="checkbox" class="user-check" value="${esc(u.user_id)}" checked>
                  <span class="user-name">${esc(u.user_name)}</span>
                  <span class="user-count">${u.message_count} 条</span>
                </label>`;
            });
            html += '</div>';
        }

        showImportPreview(html);

        document.getElementById("select-all").addEventListener("change", (e) => {
            document.querySelectorAll(".user-check").forEach((cb) => (cb.checked = e.target.checked));
        });

        document.getElementById("btn-import-confirm").disabled = false;
    } catch (e) {
        showImportPreview(`<div class="empty">预览失败: ${esc(e.message)}</div>`);
        document.getElementById("btn-import-confirm").disabled = true;
    }
}

async function confirmImport() {
    const groupId = document.getElementById("import-group-id").value.trim();
    if (!groupId) {
        showImportPreview('<div class="empty">请填写群号</div>');
        return;
    }
    if (!importToken) {
        showImportPreview('<div class="empty">请先预览文件</div>');
        return;
    }

    const checked = Array.from(document.querySelectorAll(".user-check:checked"));
    const user_ids = checked.map((cb) => cb.value);
    if (!user_ids.length) {
        showImportPreview('<div class="empty">请至少选择一个用户</div>');
        return;
    }

    showImportPreview('<div class="empty">导入中...</div>');

    try {
        const result = await bridge.apiPost("import/execute", {
            import_token: importToken,
            group_id: groupId,
            user_ids: user_ids,
        });

        const results = result.results || [];
        showImportPreview(
            `<p style="color:var(--save)">导入完成: ${results.length} 个用户</p>` +
                results.map((r) => `<p>${esc(r.user_name)}: ${r.imported} 条消息</p>`).join("")
        );
        document.getElementById("btn-import-confirm").disabled = true;
        importToken = "";
        loadPersonas();
    } catch (e) {
        showImportPreview(`<div class="empty">导入失败: ${esc(e.message)}</div>`);
    }
}

function showImportPreview(html) {
    document.getElementById("import-preview").innerHTML = html;
}

// ---- Helpers ----

function esc(s) {
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = reader.result;
            const comma = result.indexOf(",");
            resolve(comma > -1 ? result.slice(comma + 1) : result);
        };
        reader.onerror = () => reject(new Error("文件读取失败"));
        reader.readAsDataURL(file);
    });
}

// ---- Group Config ----

async function loadGroupConfigs() {
    const sel = document.getElementById("config-group-select");
    try {
        const groups = await bridge.apiGet("group_configs");
        sel.innerHTML = '<option value="">-- 选择群 --</option>';
        groups.forEach((g) => {
            sel.innerHTML += `<option value="${esc(g.group_id)}">${esc(g.group_name || g.group_id)} (${esc(g.group_id)})</option>`;
        });
    } catch (e) {
        document.getElementById("config-status").textContent = `加载失败: ${e.message}`;
    }
}

async function onConfigGroupChange() {
    const groupId = document.getElementById("config-group-select").value;
    if (!groupId) return;
    document.getElementById("config-status").textContent = "加载中...";
    try {
        const cfg = await bridge.apiGet(`group_config/${groupId}`);
        document.querySelector(`input[name="reply_mode"][value="${cfg.reply_mode || 'random'}"]`).checked = true;
        document.getElementById("at-trigger-toggle").checked = cfg.at_trigger !== false;

        const personaSel = document.getElementById("specific-persona-select");
        personaSel.innerHTML = '<option value="">-- 选择群友 --</option>';
        (cfg.personas || []).forEach((p) => {
            personaSel.innerHTML += `<option value="${esc(p.slug)}" ${cfg.specific_slug === p.slug ? 'selected' : ''}>${esc(p.name)} [${esc(p.slug)}]</option>`;
        });

        onModeChange();
        document.getElementById("config-status").textContent = "";
    } catch (e) {
        document.getElementById("config-status").textContent = `加载失败: ${e.message}`;
    }
}

function onModeChange() {
    const mode = document.querySelector("input[name='reply_mode']:checked")?.value;
    document.getElementById("specific-persona-row").style.display = mode === "specific" ? "" : "none";
}

async function saveConfig() {
    const groupId = document.getElementById("config-group-select").value;
    if (!groupId) {
        document.getElementById("config-status").textContent = "请先选择群";
        return;
    }
    const replyMode = document.querySelector("input[name='reply_mode']:checked")?.value || "random";
    const specificSlug = replyMode === "specific" ? document.getElementById("specific-persona-select").value : "";
    const atTrigger = document.getElementById("at-trigger-toggle").checked;

    document.getElementById("config-status").textContent = "保存中...";
    try {
        await bridge.apiPost(`group_config/${groupId}`, {
            reply_mode: replyMode,
            specific_slug: specificSlug,
            at_trigger: atTrigger,
        });
        document.getElementById("config-status").textContent = "已保存";
    } catch (e) {
        document.getElementById("config-status").textContent = `保存失败: ${e.message}`;
    }
}
