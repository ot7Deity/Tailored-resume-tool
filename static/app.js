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

function nav(active) {
  const links = [["index.html", "Master resume"], ["tailor.html", "Tailor to a job"]];
  document.body.insertAdjacentHTML("afterbegin",
    `<header class="nav"><span class="brand">Tailored Resume</span>` +
    links.map(([href, label]) => `<a href="${href}" class="${href === active ? "active" : ""}">${label}</a>`).join("") +
    `</header>`);
}
