const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
if (token) {
  window.history.replaceState({}, document.title, window.location.pathname);
}
let selected = new Set();
let runId = "";
let selectedRisk = "";

function api(path) {
  const joiner = path.includes("?") ? "&" : "?";
  return fetch(`${path}${joiner}token=${encodeURIComponent(token)}`).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
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
  const scan = data.scan || {};
  text("scan-meta", `扫描 ${scan.id || "-"} · ${scan.root_path || "-"}`);
  text("files", scan.files || 0);
  text("folders", scan.folders || 0);
  text("reclaimable", formatBytes(scan.reclaimable_bytes));
  text("max-depth", scan.max_depth || 0);
  text("candidate-total", `候选项 ${scan.candidate_count || 0}`);
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
  const command = ids.length === 0 ? "" : `disk-cleanup clean --run-id ${runId} ${ids.map((id) => `--candidate-id ${id}`).join(" ")}`;
  output.textContent = command || "尚未选择可执行候选项。";
  document.getElementById("copy-button").disabled = !command;
}

document.getElementById("copy-button").addEventListener("click", async () => {
  await navigator.clipboard.writeText(document.getElementById("cleanup-output").textContent);
});

Promise.all([loadSummary(), loadTree(), loadCandidates()]).catch((error) => {
  text("scan-meta", `加载失败: ${error.message}`);
});

