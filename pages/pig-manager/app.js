const bridge = window.AstrBotPluginPage;

const tabs = document.querySelectorAll(".tab");
const contents = document.querySelectorAll(".tab-content");
const statusText = document.getElementById("status-text");
const personaList = document.getElementById("persona-list");
const personaSelect = document.getElementById("persona-select");
const personaEditor = document.getElementById("persona-editor");
const editorStatus = document.getElementById("editor-status");

let personas = [];
let _previewData = null;

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
    personaList.innerHTML = '<div class="empty">还没有蒸馏任何群友。在群聊中使用 /pig distill &lt;名字&gt; 开始。</div>';
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
  const targetName = document.getElementById("import-target-name").value.trim();
  const groupId = document.getElementById("import-group-id").value.trim();
  const file = fileInput.files[0];

  if (!file) {
    showImportPreview("请选择文件");
    return;
  }

  try {
    const params = {};
    if (targetName) params.target_name = targetName;
    if (groupId) params.group_id = groupId;

    const result = await bridge.upload("import", file, params);

    if (result.preview) {
      const msgs = result.preview;
      showImportPreview(
        `<p><strong>解析到 ${result.count} 条消息</strong></p>` +
          msgs.map((m) => `<div class="msg"><span class="msg-sender">${esc(m.sender)}</span>${esc(m.content)}</div>`).join("")
      );
      _previewData = { targetName, groupId, file };
      document.getElementById("btn-import-confirm").disabled = false;
    } else if (result.imported !== undefined) {
      showImportPreview(`<p>已导入 <strong>${result.imported}</strong> 条消息 (${esc(result.user_name)})</p>`);
      document.getElementById("btn-import-confirm").disabled = true;
    }
  } catch (e) {
    showImportPreview(`导入预览失败: ${e.message}`);
  }
}

async function confirmImport() {
  const fileInput = document.getElementById("import-file");
  const targetName = document.getElementById("import-target-name").value.trim();
  const groupId = document.getElementById("import-group-id").value.trim();
  const file = fileInput.files[0];

  if (!file || !targetName || !groupId) {
    showImportPreview("请填写群号、目标昵称并选择文件");
    return;
  }

  try {
    const result = await bridge.upload("import", file, {
      target_name: targetName,
      group_id: groupId,
    });
    showImportPreview(`<p style="color:var(--save)">导入完成: ${result.imported} 条消息</p>`);
    document.getElementById("btn-import-confirm").disabled = true;
    loadPersonas();
  } catch (e) {
    showImportPreview(`导入失败: ${e.message}`);
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
