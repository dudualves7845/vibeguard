/**
 * VibeGuard Web Dashboard UI Rendering & Event Handling
 * Zero external dependencies — vanilla JavaScript.
 */

window.renderProjectHeader = function() {
  const p = window.vgState.project;
  if (!p) return;
  document.getElementById("active-project-path").textContent = p.project_path;
  const statusBadge = document.getElementById("header-status-badge");
  const hookBtn = document.getElementById("btn-toggle-hook");

  if (p.is_git_repo) {
    statusBadge.className = "badge badge-success";
    statusBadge.textContent = "GIT REPO ACTIVE";
    hookBtn.disabled = false;
    hookBtn.title = "Toggle automated pre-commit hook";
    hookBtn.innerHTML = p.git_hook_installed ? "<span>🛡️</span> Uninstall Hook" : "<span>🛡️</span> Install Hook";
  } else {
    statusBadge.className = "badge badge-warning";
    statusBadge.textContent = "NON-GIT DIRECTORY";
    hookBtn.disabled = true;
    hookBtn.title = "Initialize a Git repository to use pre-commit hooks";
    hookBtn.innerHTML = "<span>🛡️</span> No Git Repo";
  }
};

window.renderMetricCards = function() {
  const p = window.vgState.project;
  if (!p) return;

  document.getElementById("metric-symbols-val").textContent = p.total_symbols || 0;
  document.getElementById("metric-symbols-desc").textContent = `${p.total_files || 0} protected source files`;

  const bVal = document.getElementById("metric-baseline-val");
  const bDesc = document.getElementById("metric-baseline-desc");
  if (p.has_baseline && p.baseline) {
    bVal.textContent = "Captured";
    bVal.style.color = "var(--accent-emerald)";
    bDesc.textContent = `${p.baseline.total_files} files (${p.baseline.formatted_date})`;
  } else {
    bVal.textContent = "Missing";
    bVal.style.color = "var(--accent-amber)";
    bDesc.textContent = "Click 'Update Baseline' to create initial snapshot";
  }

  const hVal = document.getElementById("metric-hook-val");
  const hDesc = document.getElementById("metric-hook-desc");
  if (p.git_hook_installed) {
    hVal.textContent = "Active";
    hVal.style.color = "var(--accent-emerald)";
    hDesc.textContent = "Pre-commit hook installed & guarding commits";
  } else {
    hVal.textContent = p.is_git_repo ? "Not Installed" : "N/A (No Git)";
    hVal.style.color = "var(--text-muted)";
    hDesc.textContent = p.is_git_repo ? "Click 'Install Hook' to enable" : "Initialize git to install hook";
  }
  window.renderBaselineBanner();
};

window.renderBaselineBanner = function() {
  const banner = document.getElementById("baseline-status-banner");
  if (!banner) return;
  const p = window.vgState.project;
  if (!p) { banner.style.display = "none"; return; }
  banner.style.display = "flex";
  if (p.has_baseline && p.baseline) {
    banner.className = "baseline-banner locked";
    banner.innerHTML = `<div class="baseline-banner-icon">🛡️</div><div><div class="baseline-banner-title">Baseline Locked & Anchored</div><div class="baseline-banner-body">API contracts locked to baseline from <strong>${escapeHtml(p.baseline.formatted_date)}</strong> (${escapeHtml(p.total_symbols || 0)} symbols across ${escapeHtml(p.baseline.total_files || 0)} files).</div></div>`;
  } else {
    banner.className = "baseline-banner unlocked";
    banner.innerHTML = `<div class="baseline-banner-icon">⚠️</div><div><div class="baseline-banner-title">Unanchored — No Baseline Snapshot Found</div><div class="baseline-banner-body">Contract drift checks are unanchored. Evaluating intrinsic code invariants only. Run <code>vibeguard baseline</code> to lock contracts.</div></div>`;
  }
};

window.renderFindings = function() {
  const container = document.getElementById("findings-list");
  const query = (document.getElementById("search-findings").value || "").toLowerCase();
  const filtered = window.vgState.findings.filter(f => {
    if (window.vgState.findingsFilter !== "all" && f.severity.toLowerCase() !== window.vgState.findingsFilter) return false;
    return !query || (f.file_path || "").toLowerCase().includes(query) || (f.message || "").toLowerCase().includes(query) || (f.title || "").toLowerCase().includes(query);
  });

  if (window.vgState.findings.length === 0) {
    const p = window.vgState.project;
    const descText = (p && p.has_baseline) ? `No findings relative to the baseline in this audit scope. This does not prove overall correctness.` : "No intrinsic findings in this audit scope. Run 'Update Baseline' to anchor API contracts.";
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">✨</div><div class="empty-title">All Clear — 0 Drift Findings</div><div class="empty-desc">${escapeHtml(descText)}</div></div>`;
    return;
  }
  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state"><div class="empty-icon">🔎</div><div class="empty-title">No findings match filter</div><div class="empty-desc">Try clearing the search or switching severity chips.</div></div>`;
    return;
  }

  container.innerHTML = `<div class="data-table-wrapper"><table class="data-table findings-table"><thead><tr><th style="width:110px;">Severity</th><th style="width:200px;">File & Line</th><th style="width:140px;">Category</th><th>Issue Details</th><th style="width:320px;">Actionable Remedy</th></tr></thead><tbody>${filtered.map(item => {
    const sev = escapeHtml(item.severity.toLowerCase());
    const loc = item.line_number ? `${item.file_path}:${item.line_number}` : item.file_path;
    return `<tr class="finding-row ${sev}"><td><span class="badge badge-${sev}">${escapeHtml(item.severity)}</span></td><td class="mono"><span class="file-location" title="Click to copy path" data-copy-path="${escapeHtml(item.file_path)}">${escapeHtml(loc)}</span></td><td><span class="badge badge-info">${escapeHtml(item.category)}</span></td><td><div class="finding-title">${escapeHtml(item.title)}</div><div class="finding-message">${escapeHtml(item.message)}</div></td><td><div class="remedy-box"><div class="remedy-content">${escapeHtml(item.remediation)}</div><button class="btn btn-secondary btn-sm" data-copy-remedy="${escapeHtml(encodeURIComponent(item.remediation))}">Copy</button></div></td></tr>`;
  }).join("")}</tbody></table></div>`;
};

window.renderSymbols = function() {
  const tbody = document.getElementById("symbols-table-body");
  const query = (document.getElementById("search-symbols").value || "").toLowerCase();
  const flat = [];
  (window.vgState.symbols || []).forEach(f => (f.symbols || []).forEach(s => flat.push({ ...s, filePath: f.path, language: f.language })));
  const filtered = flat.filter(s => {
    if (window.vgState.symbolsFilter !== "all" && s.kind.toLowerCase() !== window.vgState.symbolsFilter) return false;
    return !query || (s.name || "").toLowerCase().includes(query) || (s.filePath || "").toLowerCase().includes(query);
  });
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-muted);">No symbols found matching filter.</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(s => {
    const params = s.kind === "class" ? "-" : ((s.parameters && s.parameters.length > 0) ? `(${s.parameters.join(", ")})` : "()");
    return `<tr><td><span class="badge badge-info">${escapeHtml(s.kind)}</span></td><td class="mono" style="color:var(--text-main); font-weight:600;">${escapeHtml(s.name)}</td><td class="mono" style="color:#93c5fd; max-width:300px; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(params)}</td><td class="mono"><span class="file-location" data-copy-path="${escapeHtml(s.filePath)}">${escapeHtml(s.filePath)}:${escapeHtml(s.line_number)}</span></td><td><span class="badge ${s.is_exported ? 'badge-success' : 'badge-warning'}">${s.is_exported ? 'Exported' : 'Internal'}</span></td><td><span class="badge ${s.complexity > 10 ? 'badge-error' : 'badge-info'}">${escapeHtml(s.complexity || 1)}</span></td></tr>`;
  }).join("");
};

window.renderDependencies = function() {
  const tbody = document.getElementById("deps-table-body");
  const query = (document.getElementById("search-deps").value || "").toLowerCase();
  const filtered = (window.vgState.dependencies || []).filter(d => {
    if (window.vgState.depsFilter !== "all" && d.status !== window.vgState.depsFilter) return false;
    return !query || (d.name || "").toLowerCase().includes(query) || (d.reason || "").toLowerCase().includes(query);
  });
  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-muted);">No dependencies found matching filter.</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(d => {
    const isHallucinated = d.status === "hallucinated";
    const statusBadge = isHallucinated ? `<span class="badge badge-critical">⚠️ HALLUCINATED</span>` : `<span class="badge badge-success">✅ VERIFIED</span>`;
    const filesStr = (d.files || []).slice(0, 3).join(", ") + (d.files.length > 3 ? ` (+${d.files.length - 3})` : "");
    return `<tr><td>${statusBadge}</td><td class="mono" style="color:var(--text-main); font-weight:600;">${escapeHtml(d.name)}</td><td><span class="badge badge-info">${escapeHtml(d.language)}</span></td><td><span class="badge badge-warning">${escapeHtml(d.category)}</span></td><td style="color:${isHallucinated ? 'var(--accent-rose)' : 'var(--text-secondary)'};">${escapeHtml(d.reason)}</td><td class="mono" style="font-size:11px;">${escapeHtml(filesStr)}</td></tr>`;
  }).join("");
};

window.populateConfigForm = function() {
  const cfg = (window.vgState.project && window.vgState.project.config) || {};
  document.getElementById("cfg-max-lines").value = cfg.max_file_lines || 400;
  document.getElementById("cfg-max-complexity").value = cfg.max_cyclomatic_complexity || 25;
  document.getElementById("cfg-fail-imports").checked = !!cfg.fail_on_hallucinated_imports;
  document.getElementById("cfg-fail-ghost").checked = !!cfg.fail_on_ghost_declarations;
  document.getElementById("cfg-fail-secrets").checked = !!cfg.fail_on_secret_leak;
  document.getElementById("cfg-fail-dangerous").checked = !!cfg.fail_on_dangerous_calls;
};

window.saveConfig = async function(e) {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) { btn.disabled = true; btn.innerHTML = "<span>⏳</span> Saving..."; }
  const payload = {
    max_file_lines: parseInt(document.getElementById("cfg-max-lines").value, 10),
    max_cyclomatic_complexity: parseInt(document.getElementById("cfg-max-complexity").value, 10),
    fail_on_hallucinated_imports: document.getElementById("cfg-fail-imports").checked,
    fail_on_ghost_declarations: document.getElementById("cfg-fail-ghost").checked,
    fail_on_secret_leak: document.getElementById("cfg-fail-secrets").checked,
    fail_on_dangerous_calls: document.getElementById("cfg-fail-dangerous").checked,
  };
  try {
    const data = await window.vgApi("/api/config", { method: "POST", body: JSON.stringify(payload) });
    window.showToast("💾 Invariants updated in .vibeguard.json", "success");
    window.vgState.project.config = data.config;
    await window.runCheck();
  } catch (err) {
    console.error("Save config failed:", err);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
  }
};

window.renderPrompt = function() {
  document.getElementById("ai-prompt-box").textContent = window.vgState.prompt || "No prompt available. Run a check to generate AI prompt.";
};

function setupModal() {
  const modal = document.getElementById("modal-switch-project");
  document.getElementById("btn-open-switch-modal").onclick = () => {
    document.getElementById("input-project-path").value = window.vgState.project ? window.vgState.project.project_path : "";
    modal.classList.add("active");
  };
  document.getElementById("btn-close-modal").onclick = () => modal.classList.remove("active");
  document.getElementById("btn-cancel-modal").onclick = () => modal.classList.remove("active");

  document.getElementById("form-switch-project").onsubmit = async (e) => {
    e.preventDefault();
    const newPath = document.getElementById("input-project-path").value.trim();
    if (!newPath) return;
    try {
      await window.vgApi("/api/select-project", { method: "POST", body: JSON.stringify({ path: newPath }) });
      modal.classList.remove("active");
      window.showToast(`📁 Switched to ${newPath}`, "success");
      await window.loadProject();
      await window.runCheck();
    } catch (err) { console.error("Failed to switch project:", err); }
  };
}

window.renderCheckpointsTable = function(checkpoints) {
  const tbody = document.getElementById("tbody-checkpoints");
  if (!tbody) return;
  const list = checkpoints || window.vgState.checkpoints || [];
  if (list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No checkpoints yet. Click '📸 Checkpoint' to create an AI restore point!</td></tr>`;
    return;
  }
  const now = Date.now() / 1000;
  tbody.innerHTML = list.map(cp => {
    const diff = Math.max(0, Math.floor(now - (cp.timestamp || now)));
    const timeAgo = diff < 60 ? `${diff}s ago` : (diff < 3600 ? `${Math.floor(diff/60)}m ago` : `${Math.floor(diff/3600)}h ago`);
    const healthBadge = cp.is_safety
      ? `<span class="badge" style="background:#444;color:#fff;">🛡️ Safety</span>`
      : (cp.drift_status === "clean" ? `<span class="badge badge-success">✅ Clean</span>` : (cp.drift_status === "drift_detected" ? `<span class="badge badge-danger">⚠️ Drift</span>` : `<span class="badge badge-warning">ℹ️ Unanchored</span>`));
    const msg = cp.is_safety ? `<em>${cp.message}</em>` : (cp.message || "AI Session Checkpoint");
    return `<tr><td><code>${escapeHtml(cp.id)}</code></td><td title="${escapeHtml(cp.created_at)}">${timeAgo}</td><td>${healthBadge}</td><td>${escapeHtml(msg)}</td><td>${cp.files_count || 0} files</td><td><button class="btn btn-secondary btn-sm" data-rewind-id="${escapeHtml(cp.id)}" title="Rewind workspace">⏪ Rewind</button></td></tr>`;
  }).join("");
};

document.addEventListener("click", (event) => {
  const action = event.target.closest("[data-copy-path], [data-copy-remedy], [data-rewind-id]");
  if (!action) return;
  if (action.dataset.copyPath != null) window.copyPath(action.dataset.copyPath);
  else if (action.dataset.copyRemedy != null) window.copyRemedy(action.dataset.copyRemedy);
  else if (action.dataset.rewindId != null && confirm(`Rewind to checkpoint ${action.dataset.rewindId}? (Safety point created automatically)`)) {
    window.rewindWorkspace(action.dataset.rewindId);
  }
});

document.addEventListener("DOMContentLoaded", async () => {
  ["btn-run-check", "btn-update-baseline", "btn-toggle-hook", "btn-copy-badge", "btn-copy-prompt", "btn-download-patch", "btn-copy-prompt-tab"].forEach(id => {
    const fnMap = { "btn-run-check": window.runCheck, "btn-update-baseline": window.updateBaseline, "btn-toggle-hook": window.toggleHook, "btn-copy-badge": window.copyBadge, "btn-copy-prompt": window.copyPrompt, "btn-download-patch": window.downloadPatch, "btn-copy-prompt-tab": window.copyPrompt };
    const el = document.getElementById(id);
    if (el) el.onclick = fnMap[id];
  });

  const btnCp = document.getElementById("btn-create-checkpoint");
  if (btnCp) {
    btnCp.onclick = async () => {
      const input = document.getElementById("input-checkpoint-msg");
      const msg = input ? input.value.trim() : "";
      await window.createCheckpoint(msg);
      if (input) input.value = "";
    };
  }

  const btnQkRw = document.getElementById("btn-quick-rewind");
  if (btnQkRw) {
    btnQkRw.onclick = async () => {
      if (confirm("Rewind 1 step to previous AI checkpoint? (Automatic safety backup will be created)")) {
        await window.rewindWorkspace(null, 1);
      }
    };
  }

  document.getElementById("btn-refresh").onclick = async () => {
    await window.loadProject();
    await window.runCheck();
    await window.loadCheckpoints();
    window.showToast("Refreshed", "info");
  };

  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const target = btn.getAttribute("data-tab");
      document.getElementById(target).classList.add("active");
      window.vgState.activeTab = target;
      if (target === "tab-prompt" && !window.vgState.prompt) window.loadPrompt();
      if (target === "tab-rewind") window.loadCheckpoints();
    };
  });

  const bindFilter = (selector, attr, stateProp, renderFn) => {
    document.querySelectorAll(selector).forEach(chip => {
      chip.onclick = () => {
        document.querySelectorAll(selector).forEach(c => c.classList.remove("active"));
        chip.classList.add("active");
        window.vgState[stateProp] = chip.getAttribute(attr);
        renderFn();
      };
    });
  };
  bindFilter(".filter-chip[data-filter]", "data-filter", "findingsFilter", window.renderFindings);
  document.getElementById("search-findings").oninput = window.renderFindings;
  bindFilter(".filter-chip[data-sym-filter]", "data-sym-filter", "symbolsFilter", window.renderSymbols);
  document.getElementById("search-symbols").oninput = window.renderSymbols;
  bindFilter(".filter-chip[data-dep-filter]", "data-dep-filter", "depsFilter", window.renderDependencies);
  document.getElementById("search-deps").oninput = window.renderDependencies;

  document.getElementById("form-config").onsubmit = window.saveConfig;
  setupModal();

  await window.loadProject();
  await window.runCheck();
  await window.loadCheckpoints();
});
