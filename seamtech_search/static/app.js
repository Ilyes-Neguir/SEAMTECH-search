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
const pageSize = 50;
let currentOffset = 0;
let currentQuery = "";
let hasMore = false;

loadHealth();
previewClose.addEventListener("click", () => previewDialog.close());
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentQuery = input.value.trim();
  currentOffset = 0;
  if (currentQuery) await searchPage();
});

async function searchPage() {
  statusEl.textContent = "Searching...";
  resultsEl.innerHTML = "<article class=\"result loading\">Searching the index...</article>";
  try {
    const params = new URLSearchParams({ q: currentQuery, limit: String(pageSize), offset: String(currentOffset) });
    const response = await fetch(`/search?${params}`, { headers: authHeaders() });
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    hasMore = payload.has_more;
    renderResults(payload.results);
    const first = payload.count ? currentOffset + 1 : 0;
    const last = currentOffset + payload.count;
    statusEl.textContent = payload.count ? `Showing ${first}-${last}${hasMore ? "+" : ""} result${payload.count === 1 ? "" : "s"}` : "No matching file or folder found";
    renderPager(payload.count);
  } catch (error) {
    resultsEl.innerHTML = "";
    statusEl.textContent = "Search failed. Check the access token, index, and server status.";
    console.error(error);
  }
}

async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error(await response.text());
    const health = await response.json();
    const scan = health.last_scan;
    const scanText = scan ? ` · Last scan: ${scan.status}` : " · No scan recorded";
    indexSummaryEl.textContent = `${health.documents} indexed items${scanText}`;
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
      try {
        await navigator.clipboard.writeText(button.dataset.copyPath);
        button.textContent = "Copied";
      } catch (error) {
        button.textContent = "Copy failed";
        console.error(error);
      }
      setTimeout(() => { button.textContent = label; }, 1200);
    });
  });
  document.querySelectorAll("[data-preview-path]").forEach((button) => {
    button.addEventListener("click", () => loadPreview(button.dataset.previewPath));
  });
  document.querySelectorAll("[data-open-path]").forEach((button) => {
    button.addEventListener("click", () => openPath(button.dataset.openPath, button));
  });
}

function renderPager(count) {
  const oldPager = document.querySelector("#pager");
  if (oldPager) oldPager.remove();
  if (!count && currentOffset === 0) return;
  const pager = document.createElement("nav");
  pager.id = "pager";
  pager.className = "pager";
  pager.innerHTML = `<button class="secondary" id="previous-page" type="button" ${currentOffset === 0 ? "disabled" : ""}>Previous</button><span>Page ${Math.floor(currentOffset / pageSize) + 1}</span><button class="secondary" id="next-page" type="button" ${hasMore ? "" : "disabled"}>Next</button>`;
  resultsEl.after(pager);
  pager.querySelector("#previous-page").addEventListener("click", async () => {
    currentOffset = Math.max(0, currentOffset - pageSize);
    await searchPage();
  });
  pager.querySelector("#next-page").addEventListener("click", async () => {
    if (!hasMore) return;
    currentOffset += pageSize;
    await searchPage();
  });
}

function renderResult(result) {
  const type = result.is_dir ? "Folder" : (result.extension || "File");
  const copyTarget = result.is_dir ? result.path : (result.parent_path || result.path);
  const modified = new Date(result.modified_at * 1000).toLocaleString();
  const hierarchy = buildHierarchy(result.path);
  const snippet = result.snippet ? `<div class="snippet">${sanitizeHighlightedSnippet(result.snippet)}</div>` : "";
  const extraction = !result.is_dir && result.extension ? `<span class="badge status">${escapeHtml(extractionLabel(result))}</span>` : "";
  return `
    <article class="result">
      <div class="result-header">
        <h2 class="result-title">${escapeHtml(result.name || result.path)}</h2>
        <div class="badges"><span class="badge">${escapeHtml(type)}</span><span class="badge match">${escapeHtml(formatMatchType(result.match_type))}</span>${extraction}</div>
      </div>
      <div class="hierarchy">${hierarchy.map((part) => `<span>${escapeHtml(part)}</span>`).join("")}</div>
      <div class="path">${escapeHtml(result.path)}</div>
      ${snippet}
      <div class="meta">
        <span>Modified: ${escapeHtml(modified)}</span><span>Size: ${formatBytes(result.size)}</span>
        <button class="secondary" type="button" data-preview-path="${escapeAttribute(result.path)}">${result.is_dir ? "View folder" : "Preview"}</button>
        <button class="secondary" type="button" data-open-path="${escapeAttribute(result.path)}">${result.is_dir ? "Open folder" : "Open file"}</button>
        <button class="secondary" type="button" data-copy-path="${escapeAttribute(copyTarget)}">Copy location</button>
        <button class="secondary" type="button" data-copy-path="${escapeAttribute(result.path)}">Copy full path</button>
      </div>
    </article>`;
}

function extractionLabel(result) {
  if (!result.snippet) return "Metadata / no content match";
  return result.match_type === "content" ? "Content indexed" : "Metadata indexed";
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
    setTimeout(() => { button.textContent = label; }, 1400);
  }
}

function renderFilePreview(payload) {
  return `<div class="preview-path">${escapeHtml(payload.path)}</div><pre>${escapeHtml(payload.text)}</pre>`;
}

function renderFolderPreview(payload) {
  const items = payload.children.map((child) => `<li><span class="folder-item-type">${child.is_dir ? "Folder" : "File"}</span><span>${escapeHtml(child.name)}</span><span>${child.is_dir ? "" : formatBytes(child.size)}</span></li>`).join("");
  return `<div class="preview-path">${escapeHtml(payload.path)}</div><ul class="folder-list">${items || "<li>This folder is empty.</li>"}</ul>`;
}

function buildHierarchy(path) {
  return String(path).split(/[\\/]+/).filter(Boolean).slice(-5);
}

function sanitizeHighlightedSnippet(value) {
  return escapeHtml(value).replaceAll("&lt;mark&gt;", "<mark>").replaceAll("&lt;/mark&gt;", "</mark>");
}

function formatMatchType(matchType) {
  return { exact_name: "Exact name", name: "Name", path: "Path", content: "Content" }[matchType] || "Match";
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function authHeaders() {
  const token = tokenInput.value.trim();
  return token ? { "X-SEAMTECH-TOKEN": token } : {};
}

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
