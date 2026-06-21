const bridge = window.AstrBotPluginPage;

const tabs = document.querySelectorAll(".tab");
const contents = document.querySelectorAll(".tab-content");
const statusText = document.getElementById("status-text");
const personaList = document.getElementById("persona-list");

let personas = [];
let importToken = "";

// ---- Init ----

await bridge.ready();

tabs.forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
);

document.getElementById("btn-refresh").addEventListener("click", loadPersonas);
document.getElementById("btn-import-preview").addEventListener("click", previewImport);
document.getElementById("btn-import-confirm").addEventListener("click", confirmImport);
document.getElementById("group-filter").addEventListener("change", loadPersonas);
document.getElementById("config-group-select").addEventListener("change", onConfigGroupChange);
document.querySelectorAll("input[name='reply_mode']").forEach((r) => r.addEventListener("change", onModeChange));
document.getElementById("btn-save-config").addEventListener("click", saveConfig);

// Modal bindings
const modal = document.getElementById("persona-modal");
document.getElementById("modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
document.getElementById("modal-save").addEventListener("click", savePersonaContent);
document.getElementById("modal-redistill").addEventListener("click", redistillFromModal);
document.getElementById("modal-delete").addEventListener("click", deleteFromModal);

loadPersonas();

// ---- Tab Switching ----

function switchTab(name) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    contents.forEach((c) => c.classList.toggle("active", c.id === `tab-${name}`));
    if (name === "config") loadGroupConfigs();
    if (name === "nicknames") loadNicknames();
}

// ---- Persona List ----

async function loadPersonas() {
    const filter = document.getElementById("group-filter");
    const gid = filter.value;
    if (!gid) {
        personaList.innerHTML = '<div class="empty">请先选择一个群</div>';
        statusText.textContent = "";
        return;
    }

    statusText.textContent = "加载中...";
    try {
        const [allUsers] = await Promise.all([
            bridge.apiGet("distillable"),
        ]);

        const gUsers = allUsers.filter(u => String(u.group_id) === gid);
        personas = gUsers;

        const distilledSlugs = new Set(personas.filter(p => p.distilled).map(p => p.slug));
        const distilled = gUsers.filter(u => distilledSlugs.has(u.slug));
        const pending = gUsers.filter(u => !distilledSlugs.has(u.slug) && u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);
        const notReady = gUsers.filter(u => !distilledSlugs.has(u.slug) && !u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);

        // 构建群筛选下拉
        const groups = new Map();
        for (const u of allUsers) {
            const g = String(u.group_id);
            if (!groups.has(g)) groups.set(g, u.group_name || g);
        }
        const currentVal = filter.value;
        filter.innerHTML = '<option value="">-- 请选择群 --</option>';
        for (const [gi, gn] of groups) {
            filter.innerHTML += `<option value="${esc(gi)}" ${gi === currentVal ? "selected" : ""}>${esc(gn || gi)} (${esc(gi)})</option>`;
        }

        renderPersonaList(distilled, pending, notReady);
        const total = distilled.length + pending.length + notReady.length;
        statusText.textContent = `${distilled.length} 已蒸馏 / ${pending.length} 待蒸馏 / 共 ${total} 人`;
    } catch (e) {
        statusText.textContent = `加载失败: ${e.message}`;
        personaList.innerHTML = '<div class="empty">加载失败，请检查后端连接</div>';
    }
}

function renderPersonaList(distilled, pending, notReady) {
    const html = [];

    if (distilled.length) {
        html.push(`<div class="section-title">已蒸馏群友 <span class="section-count">${distilled.length}</span></div>`);
        html.push(...distilled.map((p) => personaCard(p, true)));
    }

    if (pending.length) {
        html.push(`<div class="section-title">待蒸馏群友（已达到最少消息数） <span class="section-count">${pending.length}</span></div>`);
        html.push(...pending.map((u) => distillableCard(u)));
    }

    if (notReady.length) {
        html.push(`<div class="section-title">消息不足（尚无法蒸馏） <span class="section-count">${notReady.length}</span></div>`);
        html.push(...notReady.map((u) => distillableCard(u)));
    }

    if (!html.length) {
        personaList.innerHTML = '<div class="empty">该群暂无群友消息。让群友发言或导入聊天记录后再来查看。</div>';
        return;
    }

    personaList.innerHTML = html.join("");

    document.querySelectorAll(".btn-distill").forEach((btn) => {
        btn.addEventListener("click", () =>
            doDistill(btn.dataset.group, btn.dataset.user, btn.dataset.name, btn)
        );
    });

    document.querySelectorAll(".card-persona").forEach((card) => {
        card.addEventListener("click", (e) => {
            if (e.target.closest("button")) return;
            const slug = card.dataset.slug;
            const gi = card.dataset.group;
            const ui = card.dataset.user;
            const nm = card.dataset.name;
            openModal(slug, gi, ui, nm);
        });
    });

    document.querySelectorAll(".persona-edit-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            const card = btn.closest(".card-persona");
            if (!card) return;
            openModal(card.dataset.slug, card.dataset.group, card.dataset.user, card.dataset.name);
        });
    });
}

function personaCard(p, isDistilled) {
    const ts = (p.updated_at || p.last_distill_at || "").slice(0, 10);
    const gname = p.group_name ? `${esc(p.group_name)} (${esc(p.group_id)})` : `群 ${esc(p.group_id)}`;
    return `
    <div class="card card-persona ${isDistilled ? "distilled" : ""}" data-slug="${esc(p.slug)}" data-group="${esc(p.group_id)}" data-user="${esc(p.user_id)}" data-name="${esc(p.name)}">
      <div class="card-info">
        <div class="card-name">${esc(p.name)} <span class="slug-tag">[${esc(p.slug)}]</span></div>
        <div class="card-meta">
          ${gname} · ${p.message_count || 0} 条 · ${ts || "—"}
        </div>
      </div>
      <div class="card-actions">
        ${isDistilled
            ? `<span class="distilled-badge">&#10003; 已蒸馏</span><button class="btn btn-primary persona-edit-btn">编辑人格</button>`
            : `<button class="btn btn-primary btn-distill" data-group="${esc(p.group_id)}" data-user="${esc(p.user_id)}" data-name="${esc(p.name)}">蒸馏</button>`}
      </div>
    </div>`;
}

function distillableCard(u) {
    const lastTs = u.last_msg_at ? new Date(u.last_msg_at * 1000).toLocaleDateString("zh-CN") : "—";
    const gname = u.group_name ? `${esc(u.group_name)} (${esc(u.group_id)})` : `群 ${esc(u.group_id)}`;
    const uname = u.user_name === u.user_id ? `${esc(u.user_name)} (QQ)` : esc(u.user_name);
    const minNeeded = 50;
    const bar = Math.min(100, Math.round((u.message_count / minNeeded) * 100));
    return `
    <div class="card">
      <div class="card-info">
        <div class="card-name">${uname} <span class="user-id-tag">${esc(u.user_id)}</span></div>
        <div class="card-meta">
          ${gname} · ${u.message_count} 条 · 最后发言 ${lastTs}
          ${u.reached_threshold ? "" : `<span class="progress-bar"><span class="progress-fill" style="width:${bar}%"></span><span class="progress-text">${bar}%</span></span>`}
        </div>
      </div>
      <div class="card-actions">
        ${u.reached_threshold
            ? `<button class="btn btn-primary btn-distill" data-group="${esc(u.group_id)}" data-user="${esc(u.user_id)}" data-name="${esc(u.user_name)}">蒸馏</button>`
            : `<button class="btn" disabled>需 ${minNeeded} 条</button>`}
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

// ---- Persona Modal ----

let _modalSlug = "";
let _modalGroupId = "";
let _modalUserId = "";
let _modalName = "";

async function openModal(slug, groupId, userId, name) {
    _modalSlug = slug;
    _modalGroupId = groupId;
    _modalUserId = userId;
    _modalName = name;

    document.getElementById("modal-title").textContent = `${esc(name)} (${esc(slug)})`;
    document.getElementById("modal-meta").textContent = "加载中...";
    document.getElementById("modal-editor").value = "";
    document.getElementById("persona-modal").style.display = "flex";
    document.getElementById("modal-status").textContent = "";

    try {
        const data = await bridge.apiGet(`persona/${slug}`);
        document.getElementById("modal-editor").value = data.content || "";
        const meta = data.meta || {};
        const mc = meta.message_count || 0;
        const ts = (meta.last_distill_at || "").slice(0, 10);
        document.getElementById("modal-meta").textContent = `消息数: ${mc} · 最后蒸馏: ${ts || "—"}`;
    } catch (e) {
        document.getElementById("modal-meta").textContent = `加载失败: ${e.message}`;
    }
}

function closeModal() {
    document.getElementById("persona-modal").style.display = "none";
}

async function savePersonaContent() {
    if (!_modalSlug) return;
    const status = document.getElementById("modal-status");
    status.textContent = "保存中...";
    try {
        await bridge.apiPost(`persona/${_modalSlug}/save`, {
            content: document.getElementById("modal-editor").value,
        });
        status.textContent = "已保存";
    } catch (e) {
        status.textContent = `保存失败: ${e.message}`;
    }
}

async function redistillFromModal() {
    if (!_modalUserId || !_modalGroupId) return;
    const status = document.getElementById("modal-status");
    status.textContent = "蒸馏中...";
    try {
        const result = await bridge.apiPost("distill", {
            group_id: _modalGroupId,
            user_id: _modalUserId,
            user_name: _modalName,
        });
        status.textContent = `蒸馏完成: ${esc(result.name)}`;
        closeModal();
        loadPersonas();
    } catch (e) {
        status.textContent = `蒸馏失败: ${esc(e.message)}`;
    }
}

async function deleteFromModal() {
    if (!_modalSlug || !_modalUserId || !_modalGroupId) return;
    if (!confirm(`确定删除 ${esc(_modalName)} 的人格和所有聊天记录？此操作不可恢复。`)) return;
    const status = document.getElementById("modal-status");
    status.textContent = "删除中...";
    try {
        await bridge.apiPost("persona/delete", {
            slug: _modalSlug,
            group_id: _modalGroupId,
            user_id: _modalUserId,
        });
        closeModal();
        loadPersonas();
    } catch (e) {
        status.textContent = `删除失败: ${esc(e.message)}`;
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

// ---- Nickname Management ----

async function loadNicknames() {
    const status = document.getElementById("nickname-status");
    const tbody = document.querySelector("#nickname-table tbody");
    status.textContent = "加载中...";
    try {
        const nicks = await bridge.apiGet("nicknames");
        renderNicknameTable(nicks || {});
        status.textContent = `共 ${Object.keys(nicks || {}).length} 条映射`;
    } catch (e) {
        status.textContent = `加载失败: ${e.message}`;
        tbody.innerHTML = '<tr><td colspan="3" class="empty">加载失败</td></tr>';
    }
}

function renderNicknameTable(nicks) {
    const tbody = document.querySelector("#nickname-table tbody");
    const entries = Object.entries(nicks);
    if (!entries.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty">还没有设置任何称呼</td></tr>';
        return;
    }
    tbody.innerHTML = entries
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([uid, name]) => `
        <tr>
          <td><span class="nickname-uid">${esc(uid)}</span></td>
          <td><span class="nickname-value">${esc(name)}</span></td>
          <td>
            <div class="nickname-actions">
              <button class="btn" data-action="edit" data-uid="${esc(uid)}" data-name="${esc(name)}">编辑</button>
              <button class="btn" data-action="delete" data-uid="${esc(uid)}" data-name="${esc(name)}">删除</button>
            </div>
          </td>
        </tr>`)
        .join("");

    tbody.querySelectorAll("[data-action='edit']").forEach((btn) => {
        btn.addEventListener("click", () => openEditRow(btn.dataset.uid, btn.dataset.name));
    });
    tbody.querySelectorAll("[data-action='delete']").forEach((btn) => {
        btn.addEventListener("click", () => deleteNickname(btn.dataset.uid, btn.dataset.name));
    });

    document.getElementById("btn-nickname-add").onclick = () => openEditRow("", "");
}

function openEditRow(uid, name) {
    const tbody = document.querySelector("#nickname-table tbody");
    const isNew = !uid;
    const rows = tbody.querySelectorAll("tr");
    const lastRow = rows[rows.length - 1];

    if (lastRow && lastRow.querySelector(".nickname-add-row")) {
        lastRow.remove();
    }

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="text" class="input nickname-input-uid" placeholder="QQ号" value="${esc(uid)}" ${isNew ? "" : "disabled"} /></td>
      <td><input type="text" class="input nickname-input-name" placeholder="主称呼,别名1,别名2" value="${esc(name)}" /></td>
      <td>
        <div class="nickname-actions">
          <button class="btn btn-save nickname-btn-save">保存</button>
          <button class="btn nickname-btn-cancel">取消</button>
        </div>
      </td>`;
    tbody.appendChild(tr);

    const inputName = tr.querySelector(".nickname-input-name");
    inputName.focus();
    inputName.select();

    tr.querySelector(".nickname-btn-save").addEventListener("click", async () => {
        const newUid = tr.querySelector(".nickname-input-uid").value.trim();
        const newName = inputName.value.trim();
        if (!newUid || !newName) {
            document.getElementById("nickname-status").textContent = "QQ号和称呼不能为空";
            return;
        }
        if (isNew && !/^\d+$/.test(newUid)) {
            document.getElementById("nickname-status").textContent = "QQ号应为纯数字";
            return;
        }
        try {
            await bridge.apiPost("nickname", { user_id: newUid, nickname: newName });
            loadNicknames();
        } catch (e) {
            document.getElementById("nickname-status").textContent = `保存失败: ${e.message}`;
        }
    });

    tr.querySelector(".nickname-btn-cancel").addEventListener("click", () => {
        tr.remove();
    });

    inputName.addEventListener("keydown", (e) => {
        if (e.key === "Enter") tr.querySelector(".nickname-btn-save").click();
        if (e.key === "Escape") tr.remove();
    });
}

async function deleteNickname(uid, name) {
    if (!confirm(`确定删除 ${esc(name)} (${esc(uid)}) 的称呼映射？`)) return;
    try {
        await bridge.apiPost("nickname/delete", { user_id: uid });
        loadNicknames();
    } catch (e) {
        document.getElementById("nickname-status").textContent = `删除失败: ${e.message}`;
    }
}
