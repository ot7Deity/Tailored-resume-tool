// Shared helpers for all pages.
async function api(path, opts = {}) {
  const init = { ...opts };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function $(sel, root = document) { return root.querySelector(sel); }

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function highlight(text, words) {
  let html = esc(text);
  for (const w of [...(words || [])].sort((a, b) => b.length - a.length)) {
    if (!w) continue;
    const re = new RegExp("(" + esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
    html = html.replace(re, '<mark class="kw">$1</mark>');
  }
  return html;
}

function showBanner(el, kind, msg) {
  el.className = `banner ${kind}`;
  el.textContent = msg;
  el.classList.remove("hidden");
}

// Drag-and-drop a file anywhere on the page (works in embedded browsers that can't open a file picker).
function onFileDrop(handler) {
  let depth = 0;
  const overlay = document.createElement("div");
  overlay.className = "drop-overlay hidden";
  overlay.textContent = "Drop the file to upload";
  document.body.appendChild(overlay);
  window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types.includes("Files")) { depth++; overlay.classList.remove("hidden"); } });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; overlay.classList.add("hidden"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; overlay.classList.add("hidden");
    const f = e.dataTransfer?.files?.[0];
    if (f) handler(f);
  });
}

// Graduation date <select>: options from the server, preselecting `selected`.
function fillGradSelect(sel, options, selected) {
  const opts = [...options];
  if (selected && !opts.includes(selected)) opts.unshift(selected);
  sel.innerHTML = opts.map(d => `<option value="${esc(d)}" ${d === selected ? "selected" : ""}>${esc(d)}</option>`).join("");
}

// mirrors compiler.term_for: "May 2029" -> "Spring2029"
function termFor(grad) {
  const m = /^\s*([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})\s*$/.exec(grad || "");
  if (!m) return null;
  const mon = m[1].toLowerCase();
  const season = ["jan", "feb", "mar", "apr", "may"].includes(mon) ? "Spring" : ["jun", "jul"].includes(mon) ? "Summer" : "Fall";
  return season + m[2];
}

function nav(active) {
  const links = [["index.html", "Master resume"], ["tailor.html", "Tailor to a job"]];
  document.body.insertAdjacentHTML("afterbegin",
    `<header class="nav"><span class="brand">Tailored Resume</span>` +
    links.map(([href, label]) => `<a href="${href}" class="${href === active ? "active" : ""}">${label}</a>`).join("") +
    `</header>`);
}
