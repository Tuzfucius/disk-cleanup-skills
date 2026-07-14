const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
if (token) {
  window.history.replaceState({}, document.title, window.location.pathname);
}
let selected = new Set();
let runId = "";
let selectedRisk = "";
let chartData = { top_directories: [], extension_summary: [] };
let chartMode = localStorage.getItem("disk-cleanup-chart-mode") || "directories";
let chartLimit = Number(localStorage.getItem("disk-cleanup-chart-limit") || "10");

function api(path) {
  const joiner = path.includes("?") ? "&" : "?";
  return fetch(`${path}${joiner}token=${encodeURIComponent(token)}`, { headers: { Authorization: `Bearer ${token}` } }).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  });
}

function post(path, payload) {
  return fetch(`${path}?token=${encodeURIComponent(token)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  }).then(async (response) => {
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
    return value;
  });
}

function formatBytes(value) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = Number(value || 0);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function riskLabel(value) {
  return {
    safe_cache: "绿色 · 可再生成缓存",
    safe_redownload: "黄色 · 可重新下载",
    review: "黄色 · 需人工审查",
    protected: "红色 · 禁止执行",
  }[value] || value;
}

function text(id, value) {
  document.getElementById(id).textContent = value;
}

async function loadSummary() {
  const session = await api("/api/session");
  runId = session.run_id || "";
  const data = await api("/api/summary");
  chartData = data;
  const scan = data.scan || {};
  text("scan-meta", `扫描 ${scan.id || "-"} · ${scan.root_path || "-"}`);
  text("files", scan.files || 0);
  text("folders", scan.folders || 0);
  text("reclaimable", formatBytes(scan.reclaimable_bytes));
  text("max-depth", scan.max_depth || 0);
  text("candidate-total", `候选项 ${scan.candidate_count || 0}`);
  renderChart();
}

function renderChart() {
  const source = chartMode === "directories" ? chartData.top_directories || [] : chartData.extension_summary || [];
  const rows = source.slice(0, chartLimit);
  const max = Math.max(...rows.map((row) => Number(row.subtree_allocated_bytes || row.allocated_bytes || 0)), 1);
  const container = document.getElementById("chart");
  container.innerHTML = "";
  for (const row of rows) {
    const bytes = Number(row.subtree_allocated_bytes || row.allocated_bytes || 0);
    const item = document.createElement("div");
    item.className = "bar-row";
    item.setAttribute("role", "listitem");
    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = chartMode === "directories" ? row.full_path : row.extension;
    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("div");
    bar.className = "bar-value";
    bar.style.width = `${Math.max(2, (bytes / max) * 100)}%`;
    track.appendChild(bar);
    const value = document.createElement("span");
    value.className = "bar-size";
    value.textContent = formatBytes(bytes);
    item.append(label, track, value);
    container.appendChild(item);
  }
  for (const button of document.querySelectorAll(".chart-mode")) {
    button.classList.toggle("active", button.dataset.mode === chartMode);
  }
  document.getElementById("chart-limit").value = String(chartLimit);
}

async function loadTree(nodeId = null, container = document.getElementById("tree")) {
  const query = nodeId === null ? "" : `?node_id=${nodeId}`;
  const rows = await api(`/api/tree/children${query}`);
  container.innerHTML = "";
  for (const row of rows) {
    container.appendChild(renderTreeRow(row));
  }
}

function renderTreeRow(row) {
  const wrapper = document.createElement("div");
  const rowEl = document.createElement("div");
  rowEl.className = "tree-row";
  rowEl.setAttribute("role", "treeitem");

  const left = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = row.name;
  const path = document.createElement("div");
  path.className = "path";
  path.textContent = row.full_path;
  left.append(title, path);

  const right = document.createElement("div");
  right.className = "size";
  right.textContent = formatBytes(row.subtree_allocated_bytes);

  if (row.node_type === "directory") {
    const button = document.createElement("button");
    button.className = "tree-button";
    button.type = "button";
    button.textContent = "+";
    button.title = "展开目录";
    const childBox = document.createElement("div");
    childBox.style.paddingLeft = "16px";
    button.addEventListener("click", async () => {
      if (childBox.childElementCount > 0) {
        childBox.innerHTML = "";
        button.textContent = "+";
      } else {
        await loadTree(row.id, childBox);
        button.textContent = "-";
      }
    });
    rowEl.append(button, left, right);
    wrapper.append(rowEl, childBox);
  } else {
    rowEl.append(left, right);
    wrapper.append(rowEl);
  }
  return wrapper;
}

async function loadCandidates() {
  const rows = await api("/api/candidates?limit=100");
  const container = document.getElementById("candidates");
  container.innerHTML = "";
  for (const row of rows) {
    const item = document.createElement("article");
    item.className = "candidate";
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = row.candidate_id;
    checkbox.disabled = !["safe_cache", "safe_redownload"].includes(row.risk);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        if (selectedRisk && selectedRisk !== row.risk) {
          checkbox.checked = false;
          return;
        }
        selectedRisk = row.risk;
        selected.add(row.candidate_id);
        item.classList.add("selected");
      } else {
        selected.delete(row.candidate_id);
        item.classList.remove("selected");
        if (selected.size === 0) selectedRisk = "";
      }
      renderSelection();
    });

    const body = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${row.candidate_id} · ${row.title}`;
    const path = document.createElement("div");
    path.className = "path";
    path.textContent = row.full_path;
    const evidence = document.createElement("div");
    evidence.className = "evidence";
    evidence.textContent = row.evidence;
    body.append(title, path, evidence);

    const meta = document.createElement("div");
    meta.className = `size risk-${row.risk}`;
    meta.textContent = `${formatBytes(row.reclaimable_bytes)} · ${riskLabel(row.risk)}`;
    label.append(checkbox, body);
    item.append(label, meta);
    container.appendChild(item);
  }
}

function renderSelection() {
  const output = document.getElementById("cleanup-output");
  const ids = [...selected];
  output.textContent = ids.length === 0 ? "尚未选择可执行候选项。" : `已选择 ${ids.length} 项。生成计划后，在对话中说：执行删除勾选内容`;
  document.getElementById("plan-button").disabled = ids.length === 0;
  document.getElementById("copy-button").disabled = true;
}

document.getElementById("copy-button").addEventListener("click", async () => {
  await navigator.clipboard.writeText(document.getElementById("cleanup-output").textContent);
});

document.getElementById("plan-button").addEventListener("click", async () => {
  const output = document.getElementById("cleanup-output");
  const button = document.getElementById("plan-button");
  button.disabled = true;
  try {
    const result = await post("/api/plans", { candidate_ids: [...selected] });
    const lines = result.plans.flatMap((plan) => [
      `${plan.risk_batch} · ${formatBytes(plan.expected_reclaim_bytes)}`,
      ...plan.actions.map((action) => action.path),
      `计划哈希: ${plan.plan_hash}`,
    ]);
    output.textContent = `${lines.join("\n")}\n\n请在新一轮对话中说：执行删除勾选内容`;
    document.getElementById("copy-button").disabled = false;
  } catch (error) {
    output.textContent = `无法生成计划: ${error.message}`;
  } finally {
    button.disabled = selected.size === 0;
  }
});

for (const button of document.querySelectorAll(".chart-mode")) {
  button.addEventListener("click", () => {
    chartMode = button.dataset.mode;
    localStorage.setItem("disk-cleanup-chart-mode", chartMode);
    renderChart();
  });
}
document.getElementById("chart-limit").addEventListener("change", (event) => {
  chartLimit = Number(event.target.value);
  localStorage.setItem("disk-cleanup-chart-limit", String(chartLimit));
  renderChart();
});

Promise.all([loadSummary(), loadTree(), loadCandidates()]).catch((error) => {
  text("scan-meta", `加载失败: ${error.message}`);
});

setInterval(async () => {
  try {
    const session = await api("/api/session");
    if (["COMPLETED", "PARTIAL"].includes(session.state)) {
      document.body.replaceChildren();
      window.close();
      window.location.replace("about:blank");
    }
  } catch (_) {
    // The server closes after terminal cleanup; no retry is needed.
  }
}, 1000);

