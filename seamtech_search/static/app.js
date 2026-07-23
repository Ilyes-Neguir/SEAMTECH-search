const form = document.querySelector("#search-form");
const input = document.querySelector("#query");
const statusEl = document.querySelector("#status");
const resultsEl = document.querySelector("#results");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = input.value.trim();
  if (!query) return;

  statusEl.textContent = "Searching...";
  resultsEl.innerHTML = "";

  try {
    const response = await fetch(`/search?q=${encodeURIComponent(query)}&limit=50`);
    if (!response.ok) throw new Error(await response.text());
    const payload = await response.json();
    renderResults(payload.results);
    statusEl.textContent = `${payload.count} result${payload.count === 1 ? "" : "s"} found`;
  } catch (error) {
    statusEl.textContent = "Search failed. Check that the index exists and the server is running.";
    console.error(error);
  }
});

function renderResults(results) {
  if (!results.length) {
    resultsEl.innerHTML = `<article class="result">No matching file or folder found.</article>`;
    return;
  }

  resultsEl.innerHTML = results.map(renderResult).join("");
  document.querySelectorAll("[data-copy-path]").forEach((button) => {
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copyPath);
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = "Copy path";
      }, 1200);
    });
  });
}

function renderResult(result) {
  const type = result.is_dir ? "Folder" : (result.extension || "File");
  const modified = new Date(result.modified_at * 1000).toLocaleString();
  return `
    <article class="result">
      <div class="result-header">
        <h2 class="result-title">${escapeHtml(result.name || result.path)}</h2>
        <span class="badge">${escapeHtml(type)}</span>
      </div>
      <div class="path">${escapeHtml(result.path)}</div>
      <div class="meta">
        <span>Modified: ${escapeHtml(modified)}</span>
        <span>Size: ${formatBytes(result.size)}</span>
        <button class="secondary" type="button" data-copy-path="${escapeAttribute(result.parent_path || result.path)}">Copy path</button>
      </div>
    </article>
  `;
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

