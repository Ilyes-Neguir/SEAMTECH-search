const form = document.querySelector("#search-form");
const input = document.querySelector("#query");
const tokenInput = document.querySelector("#access-token");
const statusEl = document.querySelector("#status");
const resultsEl = document.querySelector("#results");
const indexSummaryEl = document.querySelector("#index-summary");
const previewDialog = document.querySelector("#preview-dialog");
const previewTitle = document.querySelector("#preview-title");
const previewBody = document.querySelector("#preview-body");
const previewClose = document.querySelector("#preview-close");

loadHealth();

previewClose.addEventListener("click", () => previewDialog.close());

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = input.value.trim();
  if (!query) return;

  statusEl.textContent = "Searching...";
  resultsEl.innerHTML = "";

  try {
    const response = await fetch(`/search?q=${encodeURIComponent(query)}&limit=50`, { headers: authHeaders() });
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    renderResults(payload.results);
    statusEl.textContent = `${payload.count} result${payload.count === 1 ? "" : "s"} found`;
  } catch (error) {
    statusEl.textContent = "Search failed. Check that the index exists and the server is running.";
    console.error(error);
  }
});

async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error(await response.text());
    const health = await response.json();
    indexSummaryEl.textContent = `${health.documents} indexed items`;
    indexSummaryEl.title = `${health.files} files, ${health.folders} folders`;
  } catch (error) {
    indexSummaryEl.textContent = "Index unavailable";
    console.error(error);
  }
}

function renderResults(results) {
  if (!results.length) {
    resultsEl.innerHTML = `<article class="result">No matching file or folder found.</article>`;
    return;
  }

  resultsEl.innerHTML = results.map(renderResult).join("");
  document.querySelectorAll("[data-copy-path]").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = button.textContent;
      await navigator.clipboard.writeText(button.dataset.copyPath);
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = label;
      }, 1200);
    });
  });
  document.querySelectorAll("[data-preview-path]").forEach((button) => {
    button.addEventListener("click", () => loadPreview(button.dataset.previewPath));
  });
  document.querySelectorAll("[data-open-path]").forEach((button) => {
    button.addEventListener("click", () => openPath(button.dataset.openPath, button));
  });
}

function renderResult(result) {
  const type = result.is_dir ? "Folder" : (result.extension || "File");
  const copyTarget = result.is_dir ? result.path : (result.parent_path || result.path);
  const modified = new Date(result.modified_at * 1000).toLocaleString();
  const hierarchy = buildHierarchy(result.path);
  const snippet = result.snippet ? `<div class="snippet">${sanitizeHighlightedSnippet(result.snippet)}</div>` : "";
  return `
    <article class="result">
      <div class="result-header">
        <h2 class="result-title">${escapeHtml(result.name || result.path)}</h2>
        <div class="badges">
          <span class="badge">${escapeHtml(type)}</span>
          <span class="badge match">${escapeHtml(formatMatchType(result.match_type))}</span>
        </div>
      </div>
      <div class="hierarchy">${hierarchy.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}</div>
      <div class="path">${escapeHtml(result.path)}</div>
      ${snippet}
      <div class="meta">
        <span>Modified: ${escapeHtml(modified)}</span>
        <span>Size: ${formatBytes(result.size)}</span>
        <button class="secondary" type="button" data-preview-path="${escapeAttribute(result.path)}">${result.is_dir ? "View folder" : "Preview"}</button>
        <button class="secondary" type="button" data-open-path="${escapeAttribute(result.path)}">${result.is_dir ? "Open folder" : "Open file"}</button>
        <button class="secondary" type="button" data-copy-path="${escapeAttribute(copyTarget)}">Copy location</button>
        <button class="secondary" type="button" data-copy-path="${escapeAttribute(result.path)}">Copy full path</button>
      </div>
    </article>
  `;
}

async function loadPreview(path) {
  previewTitle.textContent = "Loading...";
  previewBody.innerHTML = "";
  previewDialog.showModal();

  try {
    const response = await fetch(`/preview?path=${encodeURIComponent(path)}`, { headers: authHeaders() });
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    previewTitle.textContent = payload.name || payload.path;
    previewBody.innerHTML = payload.is_dir ? renderFolderPreview(payload) : renderFilePreview(payload);
  } catch (error) {
    previewTitle.textContent = "Preview failed";
    previewBody.textContent = "The item could not be previewed.";
    console.error(error);
  }
}

async function openPath(path, button) {
  const label = button.textContent;
  button.textContent = "Opening...";
  try {
    const response = await fetch(`/open?path=${encodeURIComponent(path)}`, { method: "POST", headers: authHeaders() });
    if (!response.ok) throw new Error(await response.text());
    button.textContent = "Opened";
  } catch (error) {
    button.textContent = "Open failed";
    console.error(error);
  } finally {
    setTimeout(() => {
      button.textContent = label;
    }, 1400);
  }
}

function renderFilePreview(payload) {
  return `
    <div class="preview-path">${escapeHtml(payload.path)}</div>
    <pre>${escapeHtml(payload.text)}</pre>
  `;
}

function renderFolderPreview(payload) {
  const items = payload.children.map((child) => `
    <li>
      <span class="folder-item-type">${child.is_dir ? "Folder" : "File"}</span>
      <span>${escapeHtml(child.name)}</span>
      <span>${child.is_dir ? "" : formatBytes(child.size)}</span>
    </li>
  `).join("");
  return `
    <div class="preview-path">${escapeHtml(payload.path)}</div>
    <ul class="folder-list">${items || "<li>This folder is empty.</li>"}</ul>
  `;
}

function buildHierarchy(path) {
  return String(path)
    .split(/[\\/]+/)
    .filter(Boolean)
    .slice(-5);
}

function sanitizeHighlightedSnippet(value) {
  return escapeHtml(value)
    .replaceAll("&lt;mark&gt;", "<mark>")
    .replaceAll("&lt;/mark&gt;", "</mark>");
}

function formatMatchType(matchType) {
  const labels = {
    exact_name: "Exact name",
    name: "Name",
    path: "Path",
    content: "Content",
  };
  return labels[matchType] || "Match";
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function authHeaders() {
  const token = tokenInput.value.trim();
  return token ? { "X-SEAMTECH-TOKEN": token } : {};
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
