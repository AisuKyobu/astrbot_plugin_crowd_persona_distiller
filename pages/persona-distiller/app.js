const bridge = window.AstrBotPluginPage;

const tabs = document.querySelectorAll(".tab");
const contents = document.querySelectorAll(".tab-content");
const statusText = document.getElementById("status-text");
const personaList = document.getElementById("persona-list");

let personas = [];
let importToken = "";

// ---- Persisted state (localStorage) ----
const LS_INITIAL_COUNT = "persona_initial_count_v1";
const LS_SORT_PER_GROUP = "persona_sort_per_group_v1";
const LS_FILTER = "persona_status_filter_v1";

function lsGet(key, fallback) {
    try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); }
    catch { return fallback; }
}
function lsSet(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch {}
}

// 首次见到 distilled slug 时的 message_count，用作「自首次增量」的基线
function trackInitialCounts(distilledList) {
    const map = lsGet(LS_INITIAL_COUNT, {});
    let changed = false;
    for (const p of distilledList) {
        if (!p.slug) continue;
        if (map[p.slug] == null) { map[p.slug] = p.message_count || 0; changed = true; }
    }
    if (changed) lsSet(LS_INITIAL_COUNT, map);
    return map;
}
function initialCountOf(slug) {
    const map = lsGet(LS_INITIAL_COUNT, {});
    return map[slug];
}

// ---- State ----
let _filterStatus = lsGet(LS_FILTER, "all");
let _currentGroupId = "";

// ---- Init ----

await bridge.ready();

tabs.forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab))
);

document.getElementById("btn-import-preview").addEventListener("click", previewImport);
document.getElementById("btn-import-confirm").addEventListener("click", confirmImport);
document.getElementById("group-filter").addEventListener("change", loadPersonas);
document.getElementById("persona-search").addEventListener("input", applySearchSort);
document.getElementById("persona-sort").addEventListener("change", () => {
    if (_currentGroupId) {
        const map = lsGet(LS_SORT_PER_GROUP, {});
        map[_currentGroupId] = document.getElementById("persona-sort").value;
        lsSet(LS_SORT_PER_GROUP, map);
    }
    applySearchSort();
});
document.getElementById("config-group-select").addEventListener("change", onConfigGroupChange);
document.querySelectorAll("input[name='reply_mode']").forEach((r) => r.addEventListener("change", onModeChange));
document.getElementById("btn-save-config").addEventListener("click", saveConfig);

// Status filter chips
document.getElementById("status-filter").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const f = chip.dataset.filter;
    if (_filterStatus === f) return;
    _filterStatus = f;
    lsSet(LS_FILTER, f);
    document.querySelectorAll("#status-filter .chip").forEach((c) =>
        c.classList.toggle("active", c.dataset.filter === f)
    );
    applySearchSort();
});

// Sync chip active class on initial load
document.querySelectorAll("#status-filter .chip").forEach((c) =>
    c.classList.toggle("active", c.dataset.filter === _filterStatus)
);

// Modal bindings
const modal = document.getElementById("persona-modal");
document.getElementById("modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
document.getElementById("modal-save").addEventListener("click", savePersonaContent);
document.getElementById("modal-redistill").addEventListener("click", redistillFromModal);
document.getElementById("modal-delete").addEventListener("click", deleteFromModal);
document.getElementById("modal-incremental").addEventListener("click", incrementalFromModal);
document.getElementById("modal-correct").addEventListener("click", openCorrectModal);
document.getElementById("modal-status-dismiss").addEventListener("click", () => {
    document.getElementById("modal-status-bar").style.display = "none";
});

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

// Nickname 搜索
document.getElementById("nickname-search").addEventListener("input", applyNicknameSearch);

loadPersonas();

// ---- Tab Switching ----

function switchTab(name) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    contents.forEach((c) => c.classList.toggle("active", c.id === `tab-${name}`));
    if (name === "config") loadGroupConfigs();
    if (name === "nicknames") loadNicknames();
}

// ---- Persona List ----

let _currentGUsers = [];

async function loadPersonas() {
    const filter = document.getElementById("group-filter");
    const gid = filter.value;

    statusText.textContent = "加载中...";
    personaList.innerHTML = '<div class="skeleton" style="height:120px;margin-bottom:8px"></div><div class="skeleton" style="height:80px;margin-bottom:8px"></div><div class="skeleton" style="height:60px"></div>';
    document.getElementById("stats-bar").style.display = "none";
    try {
        const [allUsers] = await Promise.all([
            bridge.apiGet("distillable"),
        ]);

        // 始终构建群下拉,不受 gid 影响
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
            _currentGUsers = [];
            _currentGroupId = "";
            document.getElementById("status-filter").hidden = true;
            personaList.innerHTML = '<div class="empty-illustration"><div class="empty-title">请先选择一个群</div><div class="empty-hint">选择群后将显示该群的群友列表、蒸馏状态和消息数</div></div>';
            statusText.textContent = `共 ${groups.size} 个群`;
            setSearchSortEnabled(false);
            return;
        }

        const gUsers = allUsers.filter(u => String(u.group_id) === gid);
        _currentGUsers = gUsers;
        _currentGroupId = gid;
        document.getElementById("status-filter").hidden = false;
        // 恢复该群上次选的排序
        const sortMap = lsGet(LS_SORT_PER_GROUP, {});
        const sortSel = document.getElementById("persona-sort");
        if (sortMap[gid]) sortSel.value = sortMap[gid];
        setSearchSortEnabled(true);
        applySearchSort();
    } catch (e) {
        statusText.textContent = `加载失败`;
        personaList.innerHTML = `<div class="empty-illustration"><div class="empty-title">加载失败</div><div class="empty-hint">${esc(e.message)}</div></div>`;
        _currentGUsers = [];
    }
}

function setSearchSortEnabled(enabled) {
    document.getElementById("persona-search").disabled = !enabled;
    document.getElementById("persona-sort").disabled = !enabled;
}

function applySearchSort() {
    if (!_currentGUsers.length) return;
    const query = (document.getElementById("persona-search").value || "").trim().toLowerCase();
    const sortKey = document.getElementById("persona-sort").value;

    let list = _currentGUsers.slice();
    if (query) {
        list = list.filter((u) => {
            const hay = [
                u.name, u.user_name, u.user_id,
                ...(u.aliases || []),
            ].filter(Boolean).join(" ").toLowerCase();
            return hay.includes(query);
        });
    }

    // 排序
    const cmpDistilled = (a, b) => Number(!!b.distilled) - Number(!!a.distilled);
    list.sort((a, b) => {
        switch (sortKey) {
            case "msg_asc":
                return (a.message_count || 0) - (b.message_count || 0);
            case "last_desc":
                return (b.last_msg_at || 0) - (a.last_msg_at || 0);
            case "last_asc":
                return (a.last_msg_at || 0) - (b.last_msg_at || 0);
            case "status":
                return cmpDistilled(a, b) || (b.message_count || 0) - (a.message_count || 0);
            case "msg_desc":
            default:
                return (b.message_count || 0) - (a.message_count || 0);
        }
    });

    // 三分类（来自搜索后的列表，状态过滤前的全集）
    const distilledAll = list.filter((u) => u.distilled);
    const pendingAll = list.filter((u) => !u.distilled && u.reached_threshold);
    const notReadyAll = list.filter((u) => !u.distilled && !u.reached_threshold);

    // 跟踪蒸馏卡的初始 message_count（用于"自首次增量"）
    if (distilledAll.length) trackInitialCounts(distilledAll);

    // 应用状态过滤
    let distilled = distilledAll, pending = pendingAll, notReady = notReadyAll;
    if (_filterStatus === "distilled") { pending = []; notReady = []; }
    else if (_filterStatus === "pending") { distilled = []; notReady = []; }
    else if (_filterStatus === "notready") { distilled = []; pending = []; }

    renderStatsBar(distilledAll, pendingAll, notReadyAll, list.length, query, _currentGUsers.length);
    renderFilterChips(distilledAll.length, pendingAll.length, notReadyAll.length);
    renderPersonaList(distilled, pending, notReady, query, list.length, _currentGUsers.length);
}

function renderFilterChips(dCount, pCount, nCount) {
    const total = dCount + pCount + nCount;
    const chips = document.querySelectorAll("#status-filter .chip");
    const map = { all: total, distilled: dCount, pending: pCount, notready: nCount };
    chips.forEach((c) => {
        const k = c.dataset.filter;
        const n = map[k] || 0;
        c.querySelector(".chip-count").textContent = n;
        c.style.display = n === 0 && k !== "all" ? "none" : "";
    });
}

function renderStatsBar(distilled, pending, notReady, shown, total, query) {
    const bar = document.getElementById("stats-bar");
    if (total === 0) {
        bar.style.display = "none";
        return;
    }
    bar.style.display = "";
    document.getElementById("stat-distilled").textContent = distilled.length;
    document.getElementById("stat-pending").textContent = pending.length;
    document.getElementById("stat-notready").textContent = notReady.length;
    document.getElementById("stat-total").textContent = total;

    let suffix = ` / 共 ${total} 人`;
    if (query) suffix += ` (已过滤)`;
    statusText.textContent = `${distilled.length} 已蒸馏 / ${pending.length} 可蒸馏 / ${notReady.length} 未达标${suffix}`;
}

function renderPersonaList(distilled, pending, notReady, query = "", shown = 0, total = 0) {
    const html = [];

    if (distilled.length) {
        html.push(`<div class="section-title">已蒸馏群友 <span class="section-count">${distilled.length}</span></div>`);
        html.push(...distilled.map((p) => personaCard(p, true)));
    }

    if (pending.length) {
        html.push(`<div class="pending-group">`);
        html.push(`<div class="section-title">待蒸馏群友（已达到最少消息数） <span class="section-count">${pending.length}</span></div>`);
        html.push(...pending.map((u) => distillableCard(u)));
        html.push(`</div>`);
    }

    if (notReady.length) {
        html.push(`<div class="section-title">消息不足（尚无法蒸馏） <span class="section-count">${notReady.length}</span></div>`);
        html.push(...notReady.map((u) => distillableCard(u)));
    }

    if (!html.length) {
        const filterActive = _filterStatus !== "all";
        if (filterActive && total > 0) {
            personaList.innerHTML = `<div class="empty-illustration">
                <div class="empty-title">当前筛选下没有群友</div>
                <div class="empty-hint">该群共 ${total} 人；点击「全部」查看，或调整搜索关键词</div>
            </div>`;
        } else if (query && shown === 0 && total > 0) {
            personaList.innerHTML = `<div class="empty-illustration">
                <div class="empty-title">没找到匹配「${esc(query)}」的群友</div>
                <div class="empty-hint">该群共有 ${total} 人;试试搜 QQ 号、主名或别名</div>
            </div>`;
        } else {
            personaList.innerHTML = `<div class="empty-illustration">
                <div class="empty-title">该群暂无群友消息</div>
                <div class="empty-hint">插件会自动记录群聊消息,或在「数据导入」上传 qq-chat-exporter 导出的历史聊天记录</div>
            </div>`;
        }
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
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const card = btn.closest(".card-persona");
            if (!card) return;
            openModal(card.dataset.slug, card.dataset.group, card.dataset.user, card.dataset.name);
        });
    });
}

function personaCard(p, isDistilled) {
    const gname = p.group_name ? `${esc(p.group_name)} (${esc(p.group_id)})` : `群 ${esc(p.group_id)}`;
    const aliases = p.aliases || [];
    const aliasHtml = aliases.length
        ? `<span class="alias-chips">${aliases.map(a => `<span class="alias-chip">${esc(a)}</span>`).join("")}</span>`
        : "";
    const ver = (p.version || "").toString();
    const isV1 = ver === "v1" || (!ver && !p.schema);
    const verBadge = isV1
        ? `<span class="version-badge v1" title="v1 旧格式,建议重新蒸馏以升级到 v2">v1</span>`
        : `<span class="version-badge v2" title="v2 格式(stats + 真实语料)">v2</span>`;
    const upgHint = isV1
        ? `<div class="upgrade-hint"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>v1 旧格式 — 点击"编辑人格" → "重新蒸馏" 升级到 v2</div>`
        : "";

    // 生命周期：上次蒸馏距今 + 累计 + 自首次增量
    const distillAt = p.last_distill_at || p.updated_at || "";
    let ageText = "—";
    if (distillAt) {
        const days = Math.max(0, Math.floor((Date.now() - new Date(distillAt).getTime()) / 86400000));
        ageText = days === 0 ? "今天" : days === 1 ? "1 天前" : `${days} 天前`;
    }
    const tsDisplay = distillAt ? distillAt.slice(0, 10) : "—";
    const initial = initialCountOf(p.slug);
    const deltaPart = (initial != null && (p.message_count || 0) > initial)
        ? ` · 自首次 <span class="delta-plus">+${(p.message_count || 0) - initial}</span> 条`
        : "";
    const lifeMeta = `上次蒸馏 ${tsDisplay} · ${ageText} · 累计 ${p.message_count || 0} 条${deltaPart}`;

    return `
    <div class="card card-persona ${isDistilled ? "distilled" : ""}" data-slug="${esc(p.slug)}" data-group="${esc(p.group_id)}" data-user="${esc(p.user_id)}" data-name="${esc(p.name)}">
      <div class="card-info">
        <div class="card-name">
          <span class="main-nick">${esc(p.name)}</span> ${verBadge}
          <span class="slug-tag">[${esc(p.slug)}]</span>
          ${aliasHtml}
        </div>
        <div class="card-meta">${gname}</div>
        <div class="card-meta card-meta-sub">${lifeMeta}</div>
        ${upgHint}
      </div>
      <div class="card-actions">
        ${isDistilled
            ? `<span class="distilled-badge"><svg class="icon icon-sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>已蒸馏</span><button class="btn btn-outline persona-edit-btn">编辑人格</button>`
            : `<button class="btn btn-primary btn-distill" data-group="${esc(p.group_id)}" data-user="${esc(p.user_id)}" data-name="${esc(p.name)}">蒸馏</button>`}
      </div>
    </div>`;
}

function distillableCard(u) {
    const lastTs = u.last_msg_at ? new Date(u.last_msg_at * 1000).toLocaleDateString("zh-CN") : "—";
    const gname = u.group_name ? `${esc(u.group_name)} (${esc(u.group_id)})` : `群 ${esc(u.group_id)}`;
    const nickList = (u.aliases && u.aliases.length) ? [u.name || u.user_name, ...u.aliases] : [];
    // u.name 是 API 返回的主名,user_name 是 QQ 群昵称,显示优先级: 主名 > QQ 昵称
    const displayName = u.name || (u.user_name === u.user_id ? `${u.user_name} (QQ)` : u.user_name);
    const isMainCustom = u.name && u.name !== u.user_name;  // API 给了自定义主名(来自 nickname_mappings)
    const aliasHtml = (u.aliases && u.aliases.length)
        ? `<span class="alias-chips">${u.aliases.map(a => `<span class="alias-chip">${esc(a)}</span>`).join("")}</span>`
        : "";
    const minNeeded = 50;
    const bar = Math.min(100, Math.round((u.message_count / minNeeded) * 100));
    const remaining = Math.max(0, minNeeded - (u.message_count || 0));
    return `
    <div class="card${u.reached_threshold ? "" : " not-ready"}">
      <div class="card-info">
        <div class="card-name">
          <span class="main-nick">${esc(displayName)}</span>
          ${isMainCustom ? '<span class="main-marker" title="来自 nickname_mappings 设定的主名">主</span>' : ''}
          <span class="user-id-tag">${esc(u.user_id)}</span>
          ${aliasHtml}
        </div>
        <div class="card-meta">
          ${gname} · ${u.message_count} 条 · 最后发言 ${lastTs}
          ${u.reached_threshold ? "" : `<div class="progress-bar"><div class="progress-track"><div class="progress-fill" style="width:${bar}%"></div></div><span class="progress-text">${bar}%</span></div>`}
        </div>
        ${isMainCustom ? `<div class="distill-hint">蒸馏将用「主名 + 别名」作为人格基础</div>` : ""}
      </div>
      <div class="card-actions">
        ${u.reached_threshold
            ? `<button class="btn btn-primary btn-distill" data-group="${esc(u.group_id)}" data-user="${esc(u.user_id)}" data-name="${esc(displayName)}">蒸馏</button>`
            : `<button class="btn" disabled title="还差 ${remaining} 条达到 ${minNeeded} 条阈值">还差 ${remaining} 条</button>`}
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
    if (success === undefined) setTimeout(() => div.remove(), 5000);
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
    document.getElementById("modal-status-bar").style.display = "none";

    try {
        const data = await bridge.apiGet(`persona/${slug}`);
        document.getElementById("modal-editor").value = data.content || "";
        const meta = data.meta || {};
        const mc = meta.message_count || 0;
        const ts = (meta.last_distill_at || "").slice(0, 10);
        const ver = meta.version || "未知";
        const isV1 = ver === "v1" || (ver === "未知" && !meta.schema);
        const verBadge = isV1
            ? `<span class="version-badge v1">v1</span>`
            : `<span class="version-badge v2">${esc(ver)}</span>`;
        const stats = meta.stats || null;
        const statsHtml = stats
            ? `<div class="modal-stats">${esc(stats)}</div>`
            : "";
        const v1Warn = isV1
            ? `<div class="upgrade-hint">⚠️ v1 旧格式 persona,建议点"重新蒸馏"升级到 v2(获得 stats + 真实语料增强)</div>`
            : "";
        document.getElementById("modal-meta").innerHTML = `
            <div class="modal-meta-row">${verBadge} 消息数: <b>${mc}</b> · 最后蒸馏: ${ts || "—"}</div>
            ${v1Warn}
            ${statsHtml}
        `;
    } catch (e) {
        document.getElementById("modal-meta").textContent = `加载失败: ${e.message}`;
    }
}

function closeModal() {
    document.getElementById("persona-modal").style.display = "none";
    document.getElementById("modal-status-bar").style.display = "none";
}

function showModalStatus(msg, tone = "info") {
    const bar = document.getElementById("modal-status-bar");
    bar.classList.remove("is-error", "is-success");
    if (tone === "error") bar.classList.add("is-error");
    else if (tone === "success") bar.classList.add("is-success");
    bar.style.display = "";
    document.getElementById("modal-status").innerHTML = msg;
}

async function savePersonaContent() {
    if (!_modalSlug) return;
    showModalStatus("保存中...", "info");
    try {
        await bridge.apiPost(`persona/${_modalSlug}/save`, {
            content: document.getElementById("modal-editor").value,
        });
        showModalStatus("已保存", "success");
    } catch (e) {
        showModalStatus(`保存失败: ${esc(e.message)}`, "error");
    }
}

async function redistillFromModal() {
    if (!_modalUserId || !_modalGroupId) return;
    showModalStatus("蒸馏中...", "info");
    try {
        const result = await bridge.apiPost("distill", {
            group_id: _modalGroupId,
            user_id: _modalUserId,
            user_name: _modalName,
        });
        showModalStatus(`蒸馏完成: ${esc(result.name)}`, "success");
        await openModal(result.slug, _modalGroupId, _modalUserId, result.name);
        loadPersonas();
    } catch (e) {
        showModalStatus(`蒸馏失败: ${esc(e.message)}`, "error");
    }
}

async function incrementalFromModal() {
    if (!_modalSlug || !_modalUserId || !_modalGroupId) return;
    showModalStatus("增量更新中...", "info");
    try {
        const result = await bridge.apiPost("persona/incremental", {
            slug: _modalSlug,
            group_id: _modalGroupId,
            user_id: _modalUserId,
        });
        if (result.status === "no_new_messages") {
            showModalStatus(result.message, "info");
        } else {
            showModalStatus(`增量更新完成 (${result.version})`, "success");
            await openModal(_modalSlug, _modalGroupId, _modalUserId, _modalName);
        }
    } catch (e) {
        showModalStatus(`增量更新失败: ${esc(e.message)}`, "error");
    }
}

async function deleteFromModal() {
    if (!_modalSlug || !_modalUserId || !_modalGroupId) return;
    if (!confirm(`确定删除 ${esc(_modalName)} 的人格和所有聊天记录？此操作不可恢复。`)) return;
    showModalStatus("删除中...", "info");
    try {
        await bridge.apiPost("persona/delete", {
            slug: _modalSlug,
            group_id: _modalGroupId,
            user_id: _modalUserId,
        });
        closeModal();
        loadPersonas();
    } catch (e) {
        showModalStatus(`删除失败: ${esc(e.message)}`, "error");
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
        const result = await bridge.apiPost("persona/correct", {
            slug: _modalSlug,
            correction: correctionText,
        });
        closeCorrectModal();
        await openModal(_modalSlug, _modalGroupId, _modalUserId, _modalName);
        const summary = result.summary || "";
        if (result.corrected === false) {
            showModalStatus(summary || "LLM 认为当前人格描述已准确，无需修改", "info");
        } else if (summary) {
            showModalStatus("已修正:<br>" + esc(summary).replace(/\n/g, "<br>"), "success");
        } else {
            showModalStatus("人格已修正，请检查内容", "success");
        }
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
        showImportPreview('<div class="empty-illustration"><div class="empty-title">请选择 JSON 文件</div></div>');
        return;
    }

    showImportPreview(renderImportProgress("解析中…", 0, 0));

    try {
        const base64 = await readFileAsBase64(file);
        const CHUNK_SIZE = 500 * 1024; // 500KB
        const totalChunks = Math.ceil(base64.length / CHUNK_SIZE);
        const uploadId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

        for (let i = 0; i < totalChunks; i++) {
            const chunk = base64.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
            await bridge.apiPost("import/chunk", {
                upload_id: uploadId,
                chunk_index: i,
                total_chunks: totalChunks,
                file_name: file.name,
                data: chunk,
            });
            showImportPreview(renderImportProgress("上传中", i + 1, totalChunks));
        }

        showImportPreview(renderImportProgress("解析中…", totalChunks, totalChunks));
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
        showImportPreview(`<div class="empty-illustration"><div class="empty-title" style="color:var(--danger)">预览失败</div><div class="empty-hint">${esc(e.message)}</div></div>`);
        document.getElementById("btn-import-confirm").disabled = true;
    }
}

async function confirmImport() {
    const chatType = document.querySelector("input[name='chat_type']:checked")?.value || "group";
    const groupId = document.getElementById("import-group-id").value.trim();
    if (!groupId) {
        showImportPreview('<div class="empty-illustration"><div class="empty-title">请填写群号或对方QQ</div></div>');
        return;
    }
    if (!importToken) {
        showImportPreview('<div class="empty-illustration"><div class="empty-title">请先预览文件</div></div>');
        return;
    }

    const checked = Array.from(document.querySelectorAll(".user-check:checked"));
    const user_ids = checked.map((cb) => cb.value);
    if (!user_ids.length) {
        showImportPreview('<div class="empty-illustration"><div class="empty-title">请至少选择一个用户</div></div>');
        return;
    }

    showImportPreview('<div class="import-progress"><div class="import-progress-bar"><div class="import-progress-fill" style="width:100%; background: var(--accent); animation: pulse 1.5s infinite"></div></div><div class="import-progress-meta"><span>导入中…</span><span>请稍候</span></div></div>');

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
        showImportPreview(`<div class="empty-illustration"><div class="empty-title" style="color:var(--danger)">导入失败</div><div class="empty-hint">${esc(e.message)}</div></div>`);
        importToken = "";
        document.getElementById("btn-import-confirm").disabled = true;
    }
}

function showImportPreview(html) {
    document.getElementById("import-preview").innerHTML = html;
}

function renderImportProgress(stage, current, total) {
    const pct = total > 0 ? Math.round((current / total) * 100) : 0;
    return `<div class="import-progress">
        <div class="import-progress-bar">
            <div class="import-progress-fill" style="width:${pct}%"></div>
        </div>
        <div class="import-progress-meta">
            <span>${esc(stage)}${total > 0 ? ` · ${current}/${total} 块` : ""}</span>
            <span>${pct}%</span>
        </div>
    </div>`;
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
let _nickCache = {};

async function loadNicknames() {
    const status = document.getElementById("nickname-status");
    const tbody = document.querySelector("#nickname-table tbody");
    status.textContent = "加载中...";
    try {
        const nicks = await bridge.apiGet("nicknames");
        _nickCache = nicks || {};
        applyNicknameSearch();
        status.textContent = `共 ${Object.keys(_nickCache).length} 条映射`;
    } catch (e) {
        status.textContent = `加载失败: ${esc(e.message)}`;
        tbody.innerHTML = '<tr><td colspan="3" class="empty">加载失败</td></tr>';
    }
}

function applyNicknameSearch() {
    const query = (document.getElementById("nickname-search").value || "").trim().toLowerCase();
    let entries = Object.entries(_nickCache);
    if (query) {
        entries = entries.filter(([uid, name]) =>
            String(uid).toLowerCase().includes(query) ||
            String(name).toLowerCase().includes(query)
        );
    }
    renderNicknameTable(Object.fromEntries(entries));
}

function renderNicknameTable(nicks) {
    const tbody = document.querySelector("#nickname-table tbody");
    const entries = Object.entries(nicks);

    document.getElementById("btn-nickname-add").onclick = () => openNicknameModal("", "");

    if (!entries.length) {
        const query = (document.getElementById("nickname-search").value || "").trim();
        if (query) {
            tbody.innerHTML = `<tr><td colspan="3" class="empty">没找到匹配「${esc(query)}」的称呼</td></tr>`;
        } else {
            tbody.innerHTML = '<tr><td colspan="3" class="empty">还没有设置任何称呼。<br><span style="font-size:12px;color:var(--ink-3)">点击「+ 添加称呼」为群友绑定昵称,蒸馏和回复时会自动使用</span></td></tr>';
        }
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
