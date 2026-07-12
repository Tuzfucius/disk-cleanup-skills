const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
if (token) {
  window.history.replaceState({}, document.title, window.location.pathname);
}
let selected = new Set();
let currentPlanHash = "";

function api(path) {
  const joiner = path.includes("?") ? "&" : "?";
  return fetch(`${path}${joiner}token=${encodeURIComponent(token)}`).then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  });
}

function postApi(path, payload = {}) {
  const joiner = path.includes("?") ? "&" : "?";
  return fetch(`${path}${joiner}token=${encodeURIComponent(token)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((response) => {
    if (!response.ok) {
      return response.json().then((body) => {
        throw new Error(body.error || `HTTP ${response.status}`);
      });
    }
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

function text(id, value) {
  document.getElementById(id).textContent = value;
}

async function loadSummary() {
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
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        selected.add(row.candidate_id);
        item.classList.add("selected");
      } else {
        selected.delete(row.candidate_id);
        item.classList.remove("selected");
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
    meta.textContent = `${formatBytes(row.reclaimable_bytes)} · ${row.risk}`;
    label.append(checkbox, body);
    item.append(label, meta);
    container.appendChild(item);
  }
}

function renderSelection() {
  const output = document.getElementById("cleanup-output");
  output.textContent = selected.size === 0 ? "尚未选择候选项。" : `已选择 ${selected.size} 个候选项。`;
  document.getElementById("confirm-button").disabled = true;
  document.getElementById("execute-button").disabled = true;
  currentPlanHash = "";
}

async function previewSelection() {
  const candidateIds = [...selected];
  await postApi("/api/selection", { candidate_ids: candidateIds });
  const preview = await postApi("/api/preview");
  currentPlanHash = preview.plan.plan_hash;
  document.getElementById("cleanup-output").textContent = JSON.stringify(preview, null, 2);
  document.getElementById("confirm-button").disabled = false;
  document.getElementById("execute-button").disabled = true;
}

async function confirmSelection() {
  const result = await postApi("/api/confirm", { plan_hash: currentPlanHash });
  document.getElementById("cleanup-output").textContent = JSON.stringify(result, null, 2);
  document.getElementById("execute-button").disabled = false;
}

async function executeSelection() {
  const result = await postApi("/api/execute", { plan_hash: currentPlanHash });
  document.getElementById("cleanup-output").textContent = JSON.stringify(result, null, 2);
  document.getElementById("confirm-button").disabled = true;
  document.getElementById("execute-button").disabled = true;
}

document.getElementById("preview-button").addEventListener("click", () => {
  previewSelection().catch((error) => {
    document.getElementById("cleanup-output").textContent = `预览失败: ${error.message}`;
  });
});

document.getElementById("confirm-button").addEventListener("click", () => {
  confirmSelection().catch((error) => {
    document.getElementById("cleanup-output").textContent = `确认失败: ${error.message}`;
  });
});

document.getElementById("execute-button").addEventListener("click", () => {
  executeSelection().catch((error) => {
    document.getElementById("cleanup-output").textContent = `执行失败: ${error.message}`;
  });
});

Promise.all([loadSummary(), loadTree(), loadCandidates()]).catch((error) => {
  text("scan-meta", `加载失败: ${error.message}`);
});

