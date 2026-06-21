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
document.getElementById("modal-incremental").addEventListener("click", incrementalFromModal);
document.getElementById("modal-correct").addEventListener("click", openCorrectModal);

// Correct modal bindings
const correctModal = document.getElementById("correct-modal");
document.getElementById("correct-modal-close").addEventListener("click", closeCorrectModal);
document.getElementById("correct-modal-cancel").addEventListener("click", closeCorrectModal);
correctModal.addEventListener("click", (e) => { if (e.target === correctModal) closeCorrectModal(); });
document.getElementById("correct-modal-submit").addEventListener("click", submitCorrect);

// Nickname modal bindings
const nickModal = document.getElementById("nickname-modal");
document.getElementById("nickname-modal-close").addEventListener("click", closeNicknameModal);
document.getElementById("nickname-modal-cancel").addEventListener("click", closeNicknameModal);
nickModal.addEventListener("click", (e) => { if (e.target === nickModal) closeNicknameModal(); });
document.getElementById("nickname-modal-save").addEventListener("click", saveNicknameFromModal);

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

    statusText.textContent = "加载中...";
    try {
        const [allUsers] = await Promise.all([
            bridge.apiGet("distillable"),
        ]);

        // 始终构建群下拉，不受 gid 影响
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

        if (!gid) {
            personaList.innerHTML = '<div class="empty">请先选择一个群</div>';
            statusText.textContent = `共 ${groups.size} 个群`;
            return;
        }

        const gUsers = allUsers.filter(u => String(u.group_id) === gid);
        personas = gUsers;

        const distilledSlugs = new Set(gUsers.filter(p => p.distilled).map(p => p.slug));
        const distilled = gUsers.filter(u => distilledSlugs.has(u.slug));
        const pending = gUsers.filter(u => !distilledSlugs.has(u.slug) && u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);
        const notReady = gUsers.filter(u => !distilledSlugs.has(u.slug) && !u.reached_threshold)
            .sort((a, b) => b.message_count - a.message_count);

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
        await openModal(result.slug, _modalGroupId, _modalUserId, result.name);
        loadPersonas();
    } catch (e) {
        status.textContent = `蒸馏失败: ${esc(e.message)}`;
    }
}

async function incrementalFromModal() {
    if (!_modalSlug || !_modalUserId || !_modalGroupId) return;
    const status = document.getElementById("modal-status");
    status.textContent = "增量更新中...";
    try {
        const result = await bridge.apiPost("persona/incremental", {
            slug: _modalSlug,
            group_id: _modalGroupId,
            user_id: _modalUserId,
        });
        if (result.status === "no_new_messages") {
            status.textContent = result.message;
        } else {
            status.textContent = `增量更新完成 (${result.version})`;
            await openModal(_modalSlug, _modalGroupId, _modalUserId, _modalName);
        }
    } catch (e) {
        status.textContent = `增量更新失败: ${esc(e.message)}`;
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

// ---- Correction Modal ----

let _correcting = false;

function openCorrectModal() {
    if (!_modalSlug) return;
    document.getElementById("correct-modal-title").textContent = `修正人格 - ${esc(_modalName)} (${esc(_modalSlug)})`;
    document.getElementById("correct-modal-text").value = "";
    document.getElementById("correct-modal-status").textContent = "";
    document.getElementById("correct-modal").style.display = "flex";
    document.getElementById("correct-modal-text").focus();
}

function closeCorrectModal() {
    if (_correcting) return;
    document.getElementById("correct-modal").style.display = "none";
}

async function submitCorrect() {
    if (!_modalSlug || _correcting) return;
    const correctionText = document.getElementById("correct-modal-text").value.trim();
    if (!correctionText) {
        document.getElementById("correct-modal-status").textContent = "请输入修正意见";
        return;
    }
    _correcting = true;
    const btn = document.getElementById("correct-modal-submit");
    const cancelBtn = document.getElementById("correct-modal-cancel");
    btn.disabled = true;
    btn.textContent = "修正中...";
    cancelBtn.disabled = true;
    const status = document.getElementById("correct-modal-status");
    status.textContent = "正在调用 LLM 修正人格，请稍候...";
    try {
        await bridge.apiPost("persona/correct", {
            slug: _modalSlug,
            correction: correctionText,
        });
        status.textContent = "修正完成！";
        closeCorrectModal();
        await openModal(_modalSlug, _modalGroupId, _modalUserId, _modalName);
    } catch (e) {
        status.textContent = `修正失败: ${esc(e.message)}`;
    } finally {
        _correcting = false;
        btn.disabled = false;
        btn.textContent = "提交修正";
        cancelBtn.disabled = false;
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
        const detectedChatType = summary.chat_type || "group";

        // 自动设置聊天类型
        document.querySelector(`input[name="chat_type"][value="${detectedChatType}"]`).checked = true;

        // 自动填充并锁定 group_id
        const groupIdInput = document.getElementById("import-group-id");
        groupIdInput.value = gid;
        groupIdInput.readOnly = true;

        const typeLabel = detectedChatType === "private" ? "私聊" : "群聊";
        let html = `<p><strong>${typeLabel}</strong> · ${gname ? esc(gname) + " · " : ""}解析到 <strong>${total}</strong> 条消息，<strong>${users.length}</strong> 个用户</p>`;

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
    const chatType = document.querySelector("input[name='chat_type']:checked")?.value || "group";
    const groupId = document.getElementById("import-group-id").value.trim();
    if (!groupId) {
        showImportPreview('<div class="empty">请填写群号或对方QQ</div>');
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
            chat_type: chatType,
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
        document.getElementById("name-change-toggle").checked = cfg.enable_name_change !== false;

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
    const nameChange = document.getElementById("name-change-toggle").checked;

    document.getElementById("config-status").textContent = "保存中...";
    try {
        await bridge.apiPost(`group_config/${groupId}`, {
            reply_mode: replyMode,
            specific_slug: specificSlug,
            at_trigger: atTrigger,
            enable_name_change: nameChange,
        });
        document.getElementById("config-status").textContent = "已保存";
    } catch (e) {
        document.getElementById("config-status").textContent = `保存失败: ${e.message}`;
    }
}

// ---- Nickname Management ----

let _nickEditUid = "";

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

    document.getElementById("btn-nickname-add").onclick = () => openNicknameModal("", "");

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
        btn.addEventListener("click", () => openNicknameModal(btn.dataset.uid, btn.dataset.name));
    });
    tbody.querySelectorAll("[data-action='delete']").forEach((btn) => {
        btn.addEventListener("click", () => deleteNickname(btn.dataset.uid, btn.dataset.name));
    });
}

function openNicknameModal(uid, name) {
    _nickEditUid = uid;
    document.getElementById("nickname-modal-title").textContent = uid ? "编辑称呼" : "添加称呼";
    const uidInput = document.getElementById("nickname-modal-uid");
    uidInput.value = uid;
    uidInput.disabled = !!uid;
    document.getElementById("nickname-modal-name").value = name;
    document.getElementById("nickname-modal-status").textContent = "";
    document.getElementById("nickname-modal").style.display = "flex";
    if (!uid) uidInput.focus();
    else document.getElementById("nickname-modal-name").focus();
}

function closeNicknameModal() {
    document.getElementById("nickname-modal").style.display = "none";
}

async function saveNicknameFromModal() {
    const uid = document.getElementById("nickname-modal-uid").value.trim();
    const name = document.getElementById("nickname-modal-name").value.trim();
    const status = document.getElementById("nickname-modal-status");

    if (!uid || !name) {
        status.textContent = "QQ号和称呼不能为空";
        return;
    }
    if (!_nickEditUid && !/^\d+$/.test(uid)) {
        status.textContent = "QQ号应为纯数字";
        return;
    }
    const saveUid = _nickEditUid || uid;
    try {
        await bridge.apiPost("nickname", { user_id: saveUid, nickname: name });
        closeNicknameModal();
        loadNicknames();
    } catch (e) {
        status.textContent = `保存失败: ${e.message}`;
    }
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
