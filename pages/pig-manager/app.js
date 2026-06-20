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

loadPersonas();

// ---- Tab Switching ----

function switchTab(name) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    contents.forEach((c) => c.classList.toggle("active", c.id === `tab-${name}`));
    if (name === "editor") refreshPersonaSelect();
}

// ---- Persona List ----

async function loadPersonas() {
    statusText.textContent = "加载中...";
    try {
        personas = await bridge.apiGet("personas");
        renderPersonaList();
        statusText.textContent = `共 ${personas.length} 个群友`;
    } catch (e) {
        statusText.textContent = `加载失败: ${e.message}`;
        personaList.innerHTML = '<div class="empty">加载失败，请检查后端连接</div>';
    }
}

function renderPersonaList() {
    if (!personas.length) {
        personaList.innerHTML = '<div class="empty">还没有蒸馏任何群友。在群聊中使用 /qunyou distill &lt;名字&gt; 开始。</div>';
        return;
    }

    personaList.innerHTML = personas
        .map(
            (p) => `
    <div class="card">
      <div class="card-info">
        <div class="card-name">${esc(p.name)} <span style="color:var(--text-secondary);font-weight:normal">[${esc(p.slug)}]</span></div>
        <div class="card-meta">群 ${esc(p.group_id)} · ${p.message_count || 0} 条消息 · ${(p.updated_at || "").slice(0, 10)}</div>
      </div>
      <div class="card-actions">
        <button class="btn btn-primary btn-distill" data-group="${esc(p.group_id)}" data-user="${esc(p.user_id)}" data-name="${esc(p.name)}">蒸馏</button>
      </div>
    </div>`
        )
        .join("");

    document.querySelectorAll(".btn-distill").forEach((btn) => {
        btn.addEventListener("click", () =>
            doDistill(btn.dataset.group, btn.dataset.user, btn.dataset.name)
        );
    });
}

async function doDistill(groupId, userId, userName) {
    statusText.textContent = `正在蒸馏 ${userName}...`;
    try {
        const result = await bridge.apiPost("distill", {
            group_id: groupId,
            user_id: userId,
            user_name: userName,
        });
        statusText.textContent = `蒸馏完成: [${result.slug}] ${result.name}`;
        loadPersonas();
    } catch (e) {
        statusText.textContent = `蒸馏失败: ${e.message}`;
    }
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
