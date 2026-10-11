/**
 * VibeGuard Web Dashboard API Layer
 * Zero external dependencies — vanilla JavaScript.
 */

// Application State
window.vgState = {
  project: null,
  findings: [],
  symbols: [],
  dependencies: [],
  prompt: "",
  activeTab: "tab-findings",
  findingsFilter: "all",
  symbolsFilter: "all",
  depsFilter: "all",
};

// The launcher supplies a per-server token in the URL fragment (never sent in HTTP URLs).
window.vgSessionToken = "";
try {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("token");
  window.vgSessionToken = token || window.sessionStorage.getItem("vibeguard-session") || "";
  if (token) {
    window.sessionStorage.setItem("vibeguard-session", token);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
} catch (_) {}

// UI Helper: Toast Notifications
window.showToast = function(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  const icon = type === "success" ? "✅" : (type === "error" ? "❌" : "ℹ️");
  const iconSpan = document.createElement("span");
  const messageSpan = document.createElement("span");
  iconSpan.textContent = icon;
  messageSpan.textContent = String(message);
  toast.appendChild(iconSpan);
  toast.appendChild(messageSpan);
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
};

window.escapeHtml = (s) => (s == null ? "" : String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;"));
window.copyRemedy = (enc) => navigator.clipboard.writeText(decodeURIComponent(enc)).then(() => window.showToast("Remedy copied to clipboard", "success")).catch(() => {});
window.copyPath = (p) => navigator.clipboard.writeText(p).then(() => window.showToast("File path copied to clipboard", "info")).catch(() => {});

// API Client
window.vgApi = async function(endpoint, options = {}) {
  try {
    const res = await fetch(endpoint, {
      ...options,
      headers: { "Content-Type": "application/json", "X-VibeGuard-Token": window.vgSessionToken, ...(options.headers || {}) },
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || `HTTP error ${res.status}`);
    }
    return data;
  } catch (err) {
    window.showToast(err.message, "error");
    throw err;
  }
};

// Project Operations
window.loadProject = async function() {
  try {
    const data = await window.vgApi("/api/project");
    window.vgState.project = data;
    window.renderProjectHeader();
    window.renderMetricCards();
    window.populateConfigForm();
    await window.loadSymbols();
    await window.loadDependencies();
    await window.loadPrompt();
  } catch (err) {
    console.error("Failed to load project:", err);
  }
};

window.runCheck = async function() {
  const runBtn = document.getElementById("btn-run-check");
  const scopeSelect = document.getElementById("select-scope").value;
  const origHtml = runBtn.innerHTML;

  runBtn.disabled = true;
  runBtn.innerHTML = "<span>⏳</span> Analyzing...";

  const body = {
    staged: scopeSelect === "staged",
    diff: scopeSelect === "diff",
    patch: true,
    sarif: true,
  };

  try {
    const data = await window.vgApi("/api/check", {
      method: "POST",
      body: JSON.stringify(body),
    });

    window.vgState.findings = data.report.findings || [];
    window.vgState.prompt = data.prompt || "";

    const statusVal = document.getElementById("metric-status-val");
    const statusDesc = document.getElementById("metric-status-desc");
    const statusDot = document.getElementById("status-indicator-dot");

    if (data.report.total_findings === 0) {
      statusVal.textContent = "Clean";
      statusVal.style.color = "var(--accent-emerald)";
      statusDot.className = "status-dot clean";
      if (data.report.has_baseline) {
        statusDesc.textContent = `0 regressions (${data.report.baseline_symbols_count} symbols anchored) & clean invariants`;
      } else {
        statusDesc.textContent = "0 invariant violations (unanchored baseline)";
      }
      window.showToast("✨ All clear! Codebase aligns with invariants.", "success");
    } else {
      const errCount = data.report.critical_count + data.report.error_count;
      if (errCount > 0) {
        statusVal.textContent = `${data.report.total_findings} Drift Issues`;
        statusVal.style.color = "var(--accent-rose)";
        statusDot.className = "status-dot drifted";
      } else {
        statusVal.textContent = `${data.report.total_findings} Warnings`;
        statusVal.style.color = "var(--accent-amber)";
        statusDot.className = "status-dot warning";
      }
      statusDesc.textContent = `${data.report.critical_count} critical, ${data.report.error_count} error, ${data.report.warning_count} warning`;
      window.showToast(`⚠️ Found ${data.report.total_findings} drift finding(s).`, "error");
    }

    const findingsBadge = document.getElementById("badge-findings-count");
    findingsBadge.textContent = data.report.total_findings;
    findingsBadge.className = data.report.total_findings > 0 ? "tab-badge danger" : "tab-badge";

    window.renderFindings();
    window.renderPrompt();
    await window.loadDependencies();
  } catch (err) {
    console.error("Check failed:", err);
  } finally {
    runBtn.disabled = false;
    runBtn.innerHTML = origHtml;
  }
};

window.updateBaseline = async function() {
  const btn = document.getElementById("btn-update-baseline");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> Capturing...";
  }
  try {
    const data = await window.vgApi("/api/baseline", { method: "POST" });
    window.showToast(data.message, "success");
    await window.loadProject();
    await window.runCheck();
  } catch (err) {
    console.error("Baseline update failed:", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
};

window.toggleHook = async function() {
  if (!window.vgState.project || !window.vgState.project.is_git_repo) {
    window.showToast("Current project is not a git repository", "error");
    return;
  }
  const isInstalled = window.vgState.project.git_hook_installed;
  const action = isInstalled ? "uninstall" : "install";
  const btn = document.getElementById("btn-toggle-hook");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> ${isInstalled ? "Removing..." : "Installing..."}`;
  }
  try {
    const data = await window.vgApi("/api/hook", {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    window.showToast(data.message, "success");
    await window.loadProject();
  } catch (err) {
    console.error("Hook toggle failed:", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
};

window.loadPrompt = async function() {
  try {
    const data = await window.vgApi("/api/prompt");
    window.vgState.prompt = data.prompt || "";
    window.renderPrompt();
  } catch (err) {
    console.error("Failed to load prompt:", err);
  }
};

window.copyPrompt = async function() {
  const btn = document.getElementById("btn-copy-prompt");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> Copying...";
  }
  try {
    if (!window.vgState.prompt) {
      await window.loadPrompt();
    }
    if (!window.vgState.prompt) {
      window.showToast("No prompt available for current project", "info");
      return;
    }
    await navigator.clipboard.writeText(window.vgState.prompt);
    window.showToast("📋 AI Prompt copied to clipboard!", "success");
  } catch (err) {
    window.showToast("Failed to copy to clipboard", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
};

window.downloadPatch = async function() {
  const btn = document.getElementById("btn-download-patch");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> Generating...";
  }
  try {
    const data = await window.vgApi("/api/patch", { method: "POST" });
    if (!data.patch || !data.patch.trim()) {
      window.showToast("No patch available for current findings", "info");
      return;
    }
    const blob = new Blob([data.patch], { type: "text/x-diff;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vibeguard-fix-${Date.now()}.patch`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    window.showToast("💾 Patch downloaded successfully", "success");
  } catch (err) {
    console.error("Patch download failed:", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
};

window.loadSymbols = async function() {
  try {
    const data = await window.vgApi("/api/symbols");
    window.vgState.symbols = data.files || [];
    const count = data.total_symbols || 0;
    const badge = document.getElementById("badge-symbols-count");
    if (badge) badge.textContent = count;
    const symCardVal = document.getElementById("metric-symbols-val");
    if (symCardVal && (symCardVal.textContent === "--" || symCardVal.textContent === "0")) {
      symCardVal.textContent = count;
    }
    window.renderSymbols();
  } catch (err) {
    console.error("Failed to load symbols:", err);
  }
};

window.loadDependencies = async function() {
  try {
    const data = await window.vgApi("/api/dependencies");
    window.vgState.dependencies = data.dependencies || [];
    document.getElementById("badge-deps-count").textContent = data.total_imports || 0;
    window.renderDependencies();
  } catch (err) {
    console.error("Failed to load dependencies:", err);
  }
};

window.copyBadge = async function() {
  const btn = document.getElementById("btn-copy-badge");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> Copying...";
  }
  try {
    const data = await window.vgApi("/api/badge");
    const snippet = data.markdown || "[![VibeGuard Protected](vibeguard-badge.svg)](https://github.com/vibe-guard/vibeguard)";
    let copied = false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(snippet);
        copied = true;
      } catch (_) {}
    }
    if (!copied) {
      const ta = document.createElement("textarea");
      ta.value = snippet;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      document.body.removeChild(ta);
    }
    window.showToast("🏷️ Markdown badge copied to clipboard!", "success");
  } catch (err) {
    window.showToast("Failed to copy badge: " + err.message, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
};

// Checkpoints & Rewind Operations
window.loadCheckpoints = async function() {
  try {
    const data = await window.vgApi("/api/checkpoints");
    window.vgState.checkpoints = data;
    const badge = document.getElementById("badge-rewind-count");
    if (badge) badge.textContent = (data && data.length) || 0;
    if (window.renderCheckpointsTable) window.renderCheckpointsTable(data);
    return data;
  } catch (err) {
    console.error("Failed to load checkpoints", err);
    return [];
  }
};

window.createCheckpoint = async function(message) {
  try {
    const res = await window.vgApi("/api/checkpoint", {
      method: "POST",
      body: JSON.stringify({ message: message || "" }),
    });
    window.showToast("📸 Checkpoint created: " + res.checkpoint.id, "success");
    await window.loadCheckpoints();
    return res;
  } catch (err) {
    window.showToast("Failed to create checkpoint: " + err.message, "error");
  }
};

window.rewindWorkspace = async function(checkpointId, step = 1) {
  try {
    const res = await window.vgApi("/api/rewind", {
      method: "POST",
      body: JSON.stringify({ checkpoint_id: checkpointId, step }),
    });
    const rw = res.rewind;
    window.showToast(`⏪ Rewound to ${rw.target_checkpoint.id}! (${rw.restored_count} files restored)`, "success");
    await window.loadCheckpoints();
    if (window.runCheck) await window.runCheck();
    return res;
  } catch (err) {
    window.showToast("Failed to rewind workspace: " + err.message, "error");
  }
};

