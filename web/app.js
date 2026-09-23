// JDD kunnskapsdatabase – nettside for ansatte og chatbot-demo.
// Ren JavaScript uten rammeverk. All kommunikasjon går til API-et under /api/
// (nginx videresender til FastAPI-tjenesten).

"use strict";

// ------------------------------------------------------------ Hjelpere

const $ = (selector) => document.querySelector(selector);

/** Kaller API-et og returnerer JSON. Kaster en feil med norsk melding ved feil. */
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    let message = `Feil fra serveren (${response.status})`;
    if (typeof data?.detail === "string") message = data.detail;
    else if (Array.isArray(data?.detail)) message = "Sjekk at alle felt er fylt ut riktig.";
    throw new Error(message);
  }
  return data;
}

/** Escaper tekst før den settes inn som HTML (e-posttekst kan inneholde hva som helst). */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function formatTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString("nb-NO", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString("nb-NO", { dateStyle: "short", timeStyle: "short" }) : "";
}

function percent(value) {
  return value == null ? "–" : `${Math.round(value * 100)} %`;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 4000);
}

/** Norske navn på stegene i dataflyten. */
const STEPS = {
  received: "Mottatt",
  anonymized: "Anonymisert",
  extracted: "Uttrekk",
  duplicate_check: "Duplikatsjekk",
  proposed: "Forslag",
  approved: "Godkjent",
  rejected: "Avvist",
  failed: "Feil",
};
const stepPill = (step) => `<span class="pill step-${esc(step)}">${esc(STEPS[step] || step)}</span>`;

/** Produktlisten hentes én gang og brukes i nedtrekksmenyer. */
let products = [];
async function loadProducts() {
  products = await api("/api/products");
  const options = products.map((p) => `<option value="${p.id}">${esc(p.name)} (${esc(p.sku)})</option>`).join("");
  $("#kb-product").insertAdjacentHTML("beforeend", options);
}

// ------------------------------------------------------------- Faner

let activeTab = "flow";

// Fanene har egne adresser (#dataflyt, #godkjenning, …), så tilbakeknappen og bokmerker virker.
const TAB_HASH = { flow: "dataflyt", review: "godkjenning", kb: "kunnskapsbase", chat: "chatbot" };

function showTab(name) {
  activeTab = name;
  if (location.hash.slice(1) !== TAB_HASH[name] && !location.hash.startsWith("#forslag-")) {
    history.replaceState(null, "", `#${TAB_HASH[name]}`);
  }
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => (s.hidden = s.id !== `tab-${name}`));
  if (name === "review") loadReviewList();
  if (name === "kb") searchArticles();
  if (name === "chat") $("#chat-input").focus();
}

document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    history.replaceState(null, "", `#${TAB_HASH[b.dataset.tab]}`);
    showTab(b.dataset.tab);
  })
);

/** Åpner fanen (eller forslaget, f.eks. #forslag-3) som adressen peker på. */
function openFromHash() {
  const hash = location.hash.slice(1);
  const proposal = hash.match(/^forslag-(\d+)$/);
  if (proposal) {
    showTab("review");
    openProposal(proposal[1]);
    return;
  }
  const tab = Object.keys(TAB_HASH).find((key) => TAB_HASH[key] === hash);
  showTab(tab || "flow");
}
window.addEventListener("hashchange", openFromHash);

// ---------------------------------------------------------- 1. Dataflyt

let lastEventId = 0;
let lastPendingCount = null;

async function refreshFlow() {
  try {
    const [stats, events] = await Promise.all([api("/api/stats"), api("/api/events?limit=50")]);
    renderStats(stats);
    renderEvents(events);
  } catch (err) {
    $("#mode-badge").textContent = "API utilgjengelig";
  }
}

function renderStats(s) {
  $("#mode-badge").textContent = s.mode === "KI" ? `KI-modus · ${s.model}` : "Regelmodus (uten KI)";

  const tiles = [
    ["E-poster behandlet", s.emails],
    ["Venter på godkjenning", s.proposals.pending],
    ["Godkjent", s.proposals.approved],
    ["Avvist", s.proposals.rejected],
    ["Artikler i databasen", s.articles],
    ["Feilet", s.emails_failed],
  ];
  $("#stats").innerHTML = tiles
    .map(([label, n]) => `<div class="stat"><div class="n">${n}</div><div class="l">${label}</div></div>`)
    .join("");

  const pending = s.proposals.pending;
  const badge = $("#pending-count");
  badge.textContent = pending;
  badge.hidden = pending === 0;

  // Nye forslag har kommet inn: oppdater listen hvis den ansatte ser på den.
  if (lastPendingCount !== null && pending !== lastPendingCount && activeTab === "review" && $("#review-detail").hidden) {
    loadReviewList();
  }
  lastPendingCount = pending;
}

function renderEvents(events) {
  if (events.length === 0) {
    $("#events").innerHTML = `<li class="empty">Ingen hendelser ennå. Send testdata med <code>docker compose run --rm seed</code>.</li>`;
    lastEventId = 0;
    return;
  }
  const newestSeen = lastEventId;
  $("#events").innerHTML = events
    .map((e) => {
      const isNew = newestSeen > 0 && e.id > newestSeen;
      const ref = e.email_item_id ? `<span class="email-ref">E-post #${e.email_item_id}</span>` : "";
      return `<li class="event step-${esc(e.step)}${isNew ? " new" : ""}">
          <time>${formatTime(e.created_at)}</time>
          <span>${stepPill(e.step)}</span>
          <span>${ref}${esc(e.detail)}</span>
        </li>`;
    })
    .join("");
  lastEventId = events[0].id;
}

$("#reset-btn").addEventListener("click", async () => {
  if (!confirm("Nullstille demoen? E-poster, forslag, hendelser og nye artikler slettes. Produkter og de tre startartiklene beholdes.")) return;
  try {
    const result = await api("/api/demo/reset", { method: "POST" });
    lastEventId = 0;
    toast(result.message);
    refreshFlow();
  } catch (err) {
    toast(err.message, true);
  }
});

// Lenker til Mailpit og Adminer på samme maskin som nettsiden.
$("#link-mailpit").href = `${location.protocol}//${location.hostname}:8025`;
$("#link-adminer").href = `${location.protocol}//${location.hostname}:8081/?pgsql=db&username=jdd&db=jdd`;

// -------------------------------------------------------- 2. Godkjenning

const REVIEWER_KEY = "jdd-reviewer";
try {
  $("#reviewer").value = localStorage.getItem(REVIEWER_KEY) || "";
} catch { /* localStorage kan være blokkert; feltet fungerer likevel */ }
$("#reviewer").addEventListener("input", (e) => {
  try { localStorage.setItem(REVIEWER_KEY, e.target.value.trim()); } catch { /* ignorer */ }
});

function reviewerName() {
  const name = $("#reviewer").value.trim();
  if (!name) {
    toast("Skriv inn navnet ditt før du godkjenner eller avviser.", true);
    $("#reviewer").focus();
  }
  return name;
}

function typeLabel(p) {
  return p.proposal_type === "update"
    ? `<span class="pill type-update">Oppdatering · ${percent(p.similarity)} likhet</span>`
    : `<span class="pill type-new">Ny artikkel</span>`;
}

async function loadReviewList() {
  if (location.hash.startsWith("#forslag-")) history.replaceState(null, "", "#godkjenning");
  $("#review-detail").hidden = true;
  $("#review-list").hidden = false;
  try {
    const proposals = await api("/api/proposals?status=pending");
    if (proposals.length === 0) {
      $("#review-list").innerHTML = `<div class="empty card">Ingen forslag venter på godkjenning.</div>`;
      return;
    }
    $("#review-list").innerHTML = `<div class="proposal-list">${proposals
      .map(
        (p) => `<button class="card proposal-item" data-id="${p.id}">
          <span>
            <strong>${esc(p.title)}</strong><br>
            <span class="muted">${esc(p.product?.name || "Produkt ikke gjenkjent")} · forslag #${p.id} · ${formatDate(p.created_at)}</span>
          </span>
          ${typeLabel(p)}
        </button>`
      )
      .join("")}</div>`;
    document.querySelectorAll(".proposal-item").forEach((b) => b.addEventListener("click", () => openProposal(b.dataset.id)));
  } catch (err) {
    $("#review-list").innerHTML = `<div class="empty card">${esc(err.message)}</div>`;
  }
}

async function openProposal(id) {
  let p;
  try {
    p = await api(`/api/proposals/${id}`);
  } catch (err) {
    return toast(err.message, true);
  }
  const productOptions = [`<option value="">Ukjent / ikke valgt</option>`]
    .concat(products.map((x) => `<option value="${x.id}" ${p.product?.id === x.id ? "selected" : ""}>${esc(x.name)} (${esc(x.sku)})</option>`))
    .join("");
  const m = p.matched_article;

  history.replaceState(null, "", `#forslag-${p.id}`);
  $("#review-list").hidden = true;
  const detail = $("#review-detail");
  detail.hidden = false;
  detail.innerHTML = `
    <div class="detail-head">
      <button class="button ghost" id="back-btn">← Tilbake</button>
      <h2>Forslag #${p.id}</h2>
      ${typeLabel(p)}
    </div>
    <div class="detail-grid ${m ? "has-match" : ""}">
      <div class="card">
        <h3>Anonymisert e-post</h3>
        <pre class="email-text">${esc(p.email_text || "(ingen tekst)")}</pre>
        <div class="field-label">Hendelseslogg</div>
        <ul class="mini-events">${p.events.map((e) => `<li>${stepPill(e.step)} ${esc(e.detail)}</li>`).join("")}</ul>
      </div>

      <form class="card form-grid" id="proposal-form">
        <h3>${m ? "Forslag til oppdatert artikkel" : "Forslag til ny artikkel"}</h3>
        <label>Tittel <input name="title" value="${esc(p.title)}" maxlength="200" required></label>
        <label>Produkt <select name="product_id">${productOptions}</select></label>
        <label>Problem <textarea name="problem" rows="3" required>${esc(p.problem)}</textarea></label>
        <label>Løsning <textarea name="solution" rows="7" required>${esc(p.solution)}</textarea></label>
        <label>Stikkord <input name="tags" value="${esc(p.tags || "")}" maxlength="300"></label>
        <div class="form-actions">
          <button type="submit" class="button approve">${m ? "Godkjenn og oppdater artikkel" : "Godkjenn som ny artikkel"}</button>
          <button type="button" class="button reject" id="reject-toggle">Avvis …</button>
        </div>
        <div id="reject-box" hidden>
          <label>Begrunnelse for avvisning <textarea id="reject-reason" rows="2" placeholder="F.eks. «Løsningen er ikke riktig for dette produktet»"></textarea></label>
          <div class="form-actions"><button type="button" class="button reject" id="reject-btn">Bekreft avvisning</button></div>
        </div>
      </form>

      ${
        m
          ? `<div class="card">
              <h3>Eksisterende artikkel #${m.id} (versjon ${m.version})</h3>
              <div class="similarity">${percent(p.similarity)} likhet</div>
              <div class="muted">Godkjenning oppdaterer denne artikkelen til versjon ${m.version + 1}.</div>
              <div class="field-label">Tittel</div><div>${esc(m.title)}</div>
              <div class="field-label">Problem</div><div>${esc(m.problem)}</div>
              <div class="field-label">Løsning</div><div style="white-space:pre-wrap">${esc(m.solution)}</div>
              <div class="field-label">Stikkord</div><div>${esc(m.tags || "–")}</div>
              <div class="field-label">Sist godkjent</div><div>${esc(m.approved_by)}, ${formatDate(m.approved_at)}</div>
            </div>`
          : ""
      }
    </div>`;

  $("#back-btn").addEventListener("click", loadReviewList);
  $("#reject-toggle").addEventListener("click", () => {
    $("#reject-box").hidden = !$("#reject-box").hidden;
    $("#reject-reason").focus();
  });

  $("#proposal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const reviewer = reviewerName();
    if (!reviewer) return;
    const form = new FormData(e.target);
    const productId = form.get("product_id");
    try {
      const result = await api(`/api/proposals/${p.id}/approve`, {
        method: "POST",
        body: {
          reviewer,
          title: form.get("title"),
          problem: form.get("problem"),
          solution: form.get("solution"),
          tags: form.get("tags"),
          product_id: productId ? Number(productId) : null,
        },
      });
      toast(`Godkjent. Artikkel #${result.article.id} er nå versjon ${result.article.version}.`);
      loadReviewList();
      refreshFlow();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("#reject-btn").addEventListener("click", async () => {
    const reviewer = reviewerName();
    if (!reviewer) return;
    const reason = $("#reject-reason").value.trim();
    if (!reason) {
      $("#reject-reason").focus();
      return toast("Skriv en begrunnelse for avvisningen.", true);
    }
    try {
      await api(`/api/proposals/${p.id}/reject`, { method: "POST", body: { reviewer, reason } });
      toast("Forslaget er avvist.");
      loadReviewList();
      refreshFlow();
    } catch (err) {
      toast(err.message, true);
    }
  });
}

// ------------------------------------------------------ 3. Kunnskapsbase

async function searchArticles() {
  const params = new URLSearchParams({ q: $("#kb-q").value.trim() });
  if ($("#kb-product").value) params.set("product", $("#kb-product").value);
  try {
    const articles = await api(`/api/articles?${params}`);
    if (articles.length === 0) {
      $("#kb-results").innerHTML = `<div class="empty card">Ingen artikler funnet.</div>`;
      return;
    }
    $("#kb-results").innerHTML = `<div class="articles">${articles
      .map(
        (a) => `<details class="card article">
          <summary>
            <strong>${esc(a.title)}</strong>
            <div class="meta">${esc(a.product_name || "Uten produkt")} · versjon ${a.version} · godkjent av ${esc(a.approved_by)} · ${formatDate(a.updated_at)}</div>
          </summary>
          <div class="body">
            <div class="field-label">Problem</div><p>${esc(a.problem)}</p>
            <div class="field-label">Løsning</div><p>${esc(a.solution)}</p>
            <div class="field-label">Stikkord</div><p>${esc(a.tags || "–")}</p>
          </div>
        </details>`
      )
      .join("")}</div>`;
  } catch (err) {
    $("#kb-results").innerHTML = `<div class="empty card">${esc(err.message)}</div>`;
  }
}

let searchTimer;
$("#kb-form").addEventListener("submit", (e) => {
  e.preventDefault();
  searchArticles();
});
$("#kb-q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchArticles, 300);
});
$("#kb-product").addEventListener("change", searchArticles);

// ------------------------------------------------------------ 4. Chatbot

function addBubble(html, cls) {
  const el = document.createElement("div");
  el.className = `bubble ${cls}`;
  el.innerHTML = html;
  $("#chat-log").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
  return el;
}

async function ask(question) {
  addBubble(esc(question), "user");
  const pending = addBubble("Søker i kunnskapsdatabasen …", "bot pending");
  $("#chat-suggestions").hidden = true;
  try {
    const result = await api("/api/chat", { method: "POST", body: { question } });
    const sources = result.sources.length
      ? `<div class="sources">Svaret bygger på:<ul>${result.sources
          .map((s) => `<li>Artikkel #${s.id}: ${esc(s.title)} (${esc(s.product_name || "uten produkt")}, versjon ${s.version})</li>`)
          .join("")}</ul></div>`
      : `<div class="sources">Ingen artikler brukt.</div>`;
    pending.className = "bubble bot";
    pending.innerHTML = `${esc(result.answer)}${sources}`;
  } catch (err) {
    pending.className = "bubble bot";
    pending.textContent = `Beklager, noe gikk galt: ${err.message}`;
  }
}

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const question = $("#chat-input").value.trim();
  if (!question) return;
  $("#chat-input").value = "";
  ask(question);
});
document.querySelectorAll("#chat-suggestions button").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));

// ------------------------------------------------------------- Oppstart

loadProducts()
  .catch(() => toast("Fikk ikke hentet produktlisten. Kjører API-et?", true))
  .finally(openFromHash); // produktlisten trengs i forslagsvisningen
refreshFlow();
setInterval(refreshFlow, 3000); // live-oppdatering av Dataflyt-fanen
