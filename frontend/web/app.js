/* Kasuwa frontend — talks to the FastAPI backend on the same origin. */
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString("en-NG", { maximumFractionDigits: 0 });

let RETAILERS = [];

/* ── retailers → chips ─────────────────────────────── */
async function loadRetailers() {
  try {
    const r = await fetch("/api/v1/retailers");
    RETAILERS = await r.json();
    const row = $("retailer-row");
    row.innerHTML = "";
    for (const it of RETAILERS) {
      const label = document.createElement("label");
      label.className = "retailer-chip";
      label.innerHTML =
        `<input type="checkbox" value="${it.key}" checked /> ${it.name}` +
        (it.degraded ? " ⚠️" : "");
      row.appendChild(label);
    }
  } catch { /* chips are optional */ }
}

function selectedRetailers() {
  const boxes = document.querySelectorAll("#retailer-row input:checked");
  const keys = [...boxes].map((b) => b.value);
  return keys.length ? keys : null;
}

/* ── ticker (signature) ─────────────────────────────── */
async function loadTicker() {
  try {
    const r = await fetch("/api/v1/products/recent/list?limit=14");
    const items = await r.json();
    if (!items.length) return;
    const parts = items.map(
      (t) =>
        `<span class="tick">${t.title} · <span class="p">${t.currency} ${fmt(t.price)}</span> · ${t.retailer}</span>`
    );
    // duplicate content so the glide loop is seamless
    $("ticker").innerHTML = parts.join("") + parts.join("");
    $("ticker-wrap").hidden = false;
  } catch { /* ticker is decorative */ }
}

/* ── search ─────────────────────────────── */
$("search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("search-input").value.trim();
  if (!q) return;
  const btn = $("search-btn");
  btn.disabled = true;
  btn.textContent = "Searching…";
  $("results").hidden = true;
  $("empty-note").hidden = true;

  try {
    const r = await fetch("/api/v1/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q, retailers: selectedRetailers() }),
    });
    const data = await r.json();
    renderResults(data);
    loadTicker();
  } catch {
    $("empty-note").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Compare prices";
  }
});

function renderResults(data) {
  const has = data.listings && data.listings.length;
  $("results").hidden = !has;
  $("empty-note").hidden = !!has;

  // sources
  const src = $("source-row");
  src.innerHTML = "";
  for (const s of data.sources_status || []) {
    const el = document.createElement("span");
    el.className = "source " + (s.ok ? "ok" : "fail");
    el.textContent = `${s.ok ? "●" : "○"} ${s.retailer} · ${s.listings_found} · ${s.elapsed_ms}ms`;
    src.appendChild(el);
  }
  if (!has) return;

  // stats
  const a = data.analytics;
  $("stats-row").innerHTML = a
    ? `
    <div class="stat"><div class="label">Cheapest</div><div class="value">${a.currency} ${fmt(a.min_price)}</div></div>
    <div class="stat"><div class="label">Average</div><div class="value">${a.currency} ${fmt(a.avg_price)}</div></div>
    <div class="stat"><div class="label">Highest</div><div class="value">${a.currency} ${fmt(a.max_price)}</div></div>
    <div class="stat"><div class="label">Listings</div><div class="value plain">${a.count}</div></div>`
    : "";

  // deal cards (top 6)
  const grid = $("deal-grid");
  grid.innerHTML = "";
  data.listings.slice(0, 6).forEach((x, i) => {
    const card = document.createElement("article");
    card.className = "deal" + (i === 0 ? " best" : "");
    card.innerHTML = `
      ${i === 0 ? '<span class="badge">BEST PRICE</span>' : ""}
      ${x.image_url ? `<img src="${x.image_url}" alt="" loading="lazy" onerror="this.remove()" />` : ""}
      <div class="name">${x.product}</div>
      <div class="price">${x.currency} ${fmt(x.price)}</div>
      <div class="shop">${x.retailer}</div>
      <a class="go" href="${x.url}" target="_blank" rel="noopener">View deal ↗</a>`;
    grid.appendChild(card);
  });

  // full table
  const rows = data.listings
    .map(
      (x) =>
        `<tr><td>${x.product}</td><td class="num">${x.currency} ${fmt(x.price)}</td>` +
        `<td>${x.retailer}</td><td><a href="${x.url}" target="_blank" rel="noopener">open</a></td></tr>`
    )
    .join("");
  $("all-table").innerHTML =
    `<tr><th>Product</th><th>Price</th><th>Retailer</th><th></th></tr>` + rows;
  $("all-wrap").hidden = false;
}

/* ── ask AI chat ─────────────────────────────── */
function addMsg(cls, html) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.innerHTML = html;
  $("chat").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  return el;
}

// minimal, safe markdown: bold, headings, tables handled as text lines
function mdLite(text) {
  const esc = text
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return esc
    .replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br />");
}

$("ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("ask-input").value.trim();
  if (!q) return;
  $("ask-input").value = "";
  addMsg("user", mdLite(q));
  const thinking = addMsg("ai thinking", "retrieving listings…");
  $("ask-btn").disabled = true;

  try {
    const r = await fetch("/api/v1/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    const data = await r.json();
    let html = mdLite(data.answer || "Something went wrong.");
    if (data.citations && data.citations.length) {
      html +=
        '<div class="cites">' +
        data.citations
          .map(
            (c) =>
              `<div>[${c.n}] <a href="${c.url}" target="_blank" rel="noopener">${c.title}</a>` +
              ` — <span class="p">${c.currency} ${fmt(c.price)}</span> · ${c.retailer}</div>`
          )
          .join("") +
        "</div>";
    }
    thinking.className = "msg ai";
    thinking.innerHTML = html;
    loadTicker();
  } catch {
    thinking.className = "msg ai";
    thinking.textContent =
      "The AI couldn't answer right now — try again in a moment.";
  } finally {
    $("ask-btn").disabled = false;
  }
});

/* ── boot ─────────────────────────────── */
loadRetailers();
loadTicker();
