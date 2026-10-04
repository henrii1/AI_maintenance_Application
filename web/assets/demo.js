// Onesi demo — gate + app bootstrap. Conversation, figures and tools live in Task 18's section below.
export const state = { me: null, setId: null, equipment: null, conversationId: null, busy: false,
  icons: {}, figures: [], drawings: [], view: "figures", citations: [] };

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

export async function api(path, body) {
  const opts = { credentials: "same-origin", headers: {} };
  if (body !== undefined) { opts.method = "POST"; opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  return fetch(path, opts);
}

async function errorText(resp) {
  try { const j = await resp.json(); return typeof j.detail === "string" ? j.detail : "Please check the form and try again."; }
  catch { return "Something went wrong. Please try again."; }
}

// ---------- gate ----------
function showStep(step) {
  $("#gate").hidden = false;
  $("#app").hidden = true;
  $("#new-code-note").hidden = true;
  $$(".gate-card").forEach(c => { c.hidden = c.dataset.step !== step; });
}

function setError(form, msg) {
  const el = $(".form-error", form);
  el.textContent = msg || "";
  el.hidden = !msg;
}

const GENERIC_ERROR = "Something went wrong. Please try again.";

// Run fn with the form's submit button disabled; any throw becomes a visible error.
async function guarded(form, fn) {
  const btn = $("button[type=submit]", form);
  if (btn.disabled) return;
  btn.disabled = true;
  try { await fn(); }
  catch { setError(form, GENERIC_ERROR); }
  finally { btn.disabled = false; }
}

function bindGate() {
  $$("[data-goto]").forEach(a => a.addEventListener("click", e => { e.preventDefault(); showStep(a.dataset.goto); }));

  $("#request-form").addEventListener("submit", e => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.reportValidity()) return;
    guarded(form, async () => {
      setError(form, "");
      const data = Object.fromEntries(new FormData(form));
      const resp = await api("/api/access/request", data);
      if (resp.ok) { $("#code-form").email.value = data.email; showStep("requested"); }
      else setError(form, await errorText(resp));
    });
  });

  $("#code-form").addEventListener("submit", e => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.reportValidity()) return;
    guarded(form, async () => {
      setError(form, "");
      $("#new-code-note").hidden = true;
      const resp = await api("/api/access/redeem", Object.fromEntries(new FormData(form)));
      if (!resp.ok) { setError(form, await errorText(resp)); return; }
      const me = await api("/api/me");
      if (!me.ok) { setError(form, GENERIC_ERROR); return; }
      history.replaceState(null, "", location.pathname);
      await startApp(await me.json());
    });
  });

  let newCodePending = false;
  $("#new-code").addEventListener("click", async e => {
    e.preventDefault();
    const form = $("#code-form");
    if (newCodePending || !form.email.reportValidity()) return;
    newCodePending = true;
    setError(form, "");
    try {
      await api("/api/access/new-code", { email: form.email.value });
      $("#new-code-note").hidden = false;
    } catch { setError(form, GENERIC_ERROR); }
    finally { newCodePending = false; }
  });
}

// ---------- app ----------
export function updateQuota(me) {
  const pill = $("#quota");
  pill.hidden = false;
  pill.textContent = me.is_admin ? "Admin · unlimited" : `${me.remaining} of ${me.limit} questions left today`;
}

export async function startApp(me) {
  state.me = me;
  $("#gate").hidden = true;
  $("#app").hidden = false;
  $("#who").textContent = me.email;
  $("#who").hidden = me.is_admin;
  $("#signout").hidden = false;
  updateQuota(me);
  const sets = await (await api("/api/sets")).json();
  const sel = $("#set-select");
  sel.innerHTML = "";
  for (const s of sets) sel.add(new Option(s.name, s.id));
  sel.hidden = sets.length < 2;
  selectSet(sets[0]);
  sel.addEventListener("change", () => selectSet(sets.find(s => s.id === sel.value)));
  document.dispatchEvent(new CustomEvent("onesi:ready"));
}

function selectSet(set) {
  state.setId = set.id;
  state.equipment = set.equipment;
  $("#equip-name").textContent = set.name;
  document.dispatchEvent(new CustomEvent("onesi:set-changed"));
}

async function init() {
  try { state.icons = await (await fetch("assets/icons.json")).json(); } catch { state.icons = {}; }
  bindGate();
  $("#signout").addEventListener("click", async () => {
    await api("/api/access/logout", {});
    await api("/api/admin/logout", {});
    location.href = "/demo";
  });
  const params = new URLSearchParams(location.search);
  const me = await api("/api/me");
  if (me.ok) { startApp(await me.json()); return; }
  if (params.get("code")) {
    const form = $("#code-form");
    form.email.value = params.get("email") || "";
    form.code.value = params.get("code");
    showStep("code");
  } else {
    showStep("request");
  }
}

init();

// =====================================================================================
// Workspace: chat stream, figure viewer, drawings, tools, mobile tabs, lightbox
// =====================================================================================
const GROUP_LABELS = { oem: "OEM tools", power: "Power tools", hand: "Hand tools", measuring: "Measuring", consumables: "Consumables" };
const CITE_RE = /\[p\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\]/g;
const narrow = window.matchMedia("(max-width: 899px)");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

function iconSvg(key) {
  const d = state.icons[key] || state.icons["generic-tool"] || "";
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  for (const [k, v] of Object.entries({ d, fill: "none", stroke: "currentColor", "stroke-width": "1.6",
    "stroke-linecap": "round", "stroke-linejoin": "round" })) p.setAttribute(k, v);
  svg.append(p);
  return svg;
}

// ---------- mobile tabs ----------
function activatePane(name) {
  $("#app").dataset.active = name;
  $$(".tabs [data-pane]").forEach(b => {
    b.setAttribute("aria-selected", String(b.dataset.pane === name));
    if (b.dataset.pane === name) $(".badge-dot", b)?.setAttribute("hidden", "");
  });
}
function flagPane(name) {
  if (!narrow.matches || $("#app").dataset.active === name) return;
  $(`.tabs [data-pane="${name}"] .badge-dot`)?.removeAttribute("hidden");
}

// ---------- viewer ----------
function showAsset(asset) {
  const viewer = $("#viewer");
  viewer.innerHTML = "";
  if (!asset) { viewer.append(el("p", { class: "empty", text: "No figure for this answer." })); $("#viewer-meta").innerHTML = ""; return; }
  const img = el("img", { src: asset.image_url, alt: asset.caption, onclick: () => openLightbox(asset) });
  viewer.append(img);
  const meta = $("#viewer-meta");
  meta.innerHTML = "";
  meta.append(el("b", { text: asset.caption }),
    el("span", { text: `${asset.asset_type === "cad" ? "Drawing" : "Manual"} · p.${asset.page} · ` }),
    el("a", { href: asset.pdf_url, target: "_blank", rel: "noopener", text: "Open in manual ↗" }));
  $$("#thumbs .thumb").forEach(t => t.setAttribute("aria-current", String(t.dataset.id === asset.id)));
}

function renderThumbs(list) {
  const box = $("#thumbs");
  box.innerHTML = "";
  for (const a of list) {
    box.append(el("button", { class: "thumb", type: "button", "data-id": a.id, title: a.caption, onclick: () => showAsset(a) },
      el("img", { src: a.thumb_url, alt: "" }), el("span", { text: a.caption })));
  }
}

function setView(view) {
  state.view = view;
  $$(".seg [data-view]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  const list = view === "figures" ? state.figures : state.drawings;
  renderThumbs(list);
  showAsset(list[0] || null);
  if (!list.length) $("#viewer").innerHTML = `<p class="empty">${view === "figures"
    ? "Ask a question and the most relevant manual figure appears here." : "No drawings for this equipment."}</p>`;
}

function showFigures(figures) {
  // merge: newest answer's figures first, keep earlier ones from this job after them
  if (!figures.length) return;
  const seen = new Set(figures.map(f => f.id));
  state.figures = [...figures, ...state.figures.filter(f => !seen.has(f.id))];
  if (state.view !== "figures") setView("figures");
  renderThumbs(state.figures);
  showAsset(state.figures[0] || null);
  flagPane("diagram");
}

async function loadDrawings() {
  const resp = await api(`/api/sets/${encodeURIComponent(state.setId)}/assets?type=cad`);
  state.drawings = resp.ok ? await resp.json() : [];
}

// ---------- lightbox with zoom/pan ----------
const lb = { scale: 1, x: 0, y: 0, pointers: new Map(), startDist: 0, startScale: 1 };
function applyLb() { $("#lightbox img").style.transform = `translate(${lb.x}px, ${lb.y}px) scale(${lb.scale})`; }
function openLightbox(asset) {
  Object.assign(lb, { scale: 1, x: 0, y: 0 });
  const img = $("#lightbox img");
  img.src = asset.image_url;
  img.alt = asset.caption;
  applyLb();
  $("#lightbox").hidden = false;
}
function bindLightbox() {
  const box = $("#lightbox"), stage = $(".lb-stage", box);
  const close = () => { box.hidden = true; };
  $(".lb-close", box).addEventListener("click", close);
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    lb.scale = Math.min(8, Math.max(1, lb.scale * Math.pow(1.0015, -e.deltaY)));
    applyLb();
  }, { passive: false });
  stage.addEventListener("dblclick", () => { Object.assign(lb, { scale: 1, x: 0, y: 0 }); applyLb(); });
  stage.addEventListener("pointerdown", e => { stage.setPointerCapture(e.pointerId); lb.pointers.set(e.pointerId, e); if (lb.pointers.size === 2) { const [a, b] = [...lb.pointers.values()]; lb.startDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); lb.startScale = lb.scale; } });
  stage.addEventListener("pointermove", e => {
    const prev = lb.pointers.get(e.pointerId);
    if (!prev) return;
    lb.pointers.set(e.pointerId, e);
    if (lb.pointers.size === 2) {
      const [a, b] = [...lb.pointers.values()];
      lb.scale = Math.min(8, Math.max(1, lb.startScale * Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) / lb.startDist));
    } else { lb.x += e.clientX - prev.clientX; lb.y += e.clientY - prev.clientY; }
    applyLb();
  });
  const up = e => lb.pointers.delete(e.pointerId);
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
}

// ---------- tools ----------
function toolsSkeleton() {
  const box = $("#tools");
  box.innerHTML = "";
  const grid = el("div", { class: "tool-grid" });
  for (let i = 0; i < 6; i++) grid.append(el("div", { class: "skeleton" }));
  box.append(grid);
}

function sourceChip(t) {
  if (t.source === "manual") return el("span", { class: "src manual", text: `Manual${t.source_ref ? " " + t.source_ref : ""}` });
  if (t.source === "web" && t.source_ref && t.source_ref.startsWith("https://"))
    return el("a", { class: "src", href: t.source_ref, target: "_blank", rel: "noopener noreferrer", text: "Web ↗" });
  return el("span", { class: "src", text: t.source === "web" ? "Web" : "General knowledge" });
}

function renderTools(groups, figures) {
  const byId = Object.fromEntries((figures || []).map(f => [f.id, f]));
  const box = $("#tools");
  box.innerHTML = "";
  if (!groups.length) { box.append(el("p", { class: "empty", text: "No specific tools identified for this question." })); return; }
  for (const g of groups) {
    const grid = el("div", { class: "tool-grid" });
    for (const t of g.tools) {
      const fig = t.figure_id && byId[t.figure_id];
      const ico = el("div", { class: "ico" }, fig ? el("img", { src: fig.thumb_url, alt: "" }) : iconSvg(t.icon));
      const card = el(fig ? "button" : "div", { class: `tool${fig ? " has-figure" : ""}`, type: fig ? "button" : null,
        title: fig ? "Show figure" : null, onclick: fig ? () => { showFigures([fig]); activatePane("diagram"); } : null },
        ico, el("b", { text: t.name }), t.purpose ? el("span", { class: "purpose", text: t.purpose }) : null,
        t.spec ? el("span", { class: "spec", text: t.spec }) : null, sourceChip(t));
      grid.append(card);
    }
    box.append(el("section", { class: "tool-group" }, el("h3", { text: GROUP_LABELS[g.category] || g.category }), grid));
  }
  flagPane("tools");
}

function toolsError(detail) {
  const box = $("#tools");
  box.innerHTML = "";
  const retry = el("button", { class: "btn btn-primary", type: "button", text: "Retry", onclick: async () => {
    if (retry.disabled) return;
    retry.disabled = true;
    let failure = null;
    try {
      toolsSkeleton();
      const resp = await api("/api/tools/retry", { conversation_id: state.conversationId });
      if (resp.ok) { const j = await resp.json(); renderTools(j.groups, j.figures); return; }
      failure = (await resp.json().catch(() => ({}))).detail;
    } catch { /* network failure: fall through to the error card */ }
    toolsError(typeof failure === "string" ? failure : null);
  } });
  box.append(el("p", { class: "tools-error", text: detail || "Couldn't load tools." }), retry);
}

// ---------- chat ----------
DOMPurify.addHook("afterSanitizeAttributes", node => {
  if (node.tagName === "A") {
    const href = node.getAttribute("href") || "";
    if (!href.startsWith("https://")) node.removeAttribute("href");
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

function markdown(text) {
  const withCites = text.replace(CITE_RE, (_, p) => `<button class="cite" type="button" data-page="${p}">p.${p}</button>`);
  return DOMPurify.sanitize(marked.parse(withCites), { ADD_ATTR: ["data-page"], FORBID_TAGS: ["img"] });
}

function addMessage(role, text) {
  $("#starter").hidden = true;
  const node = el("div", { class: `msg ${role}` });
  if (role === "user") node.textContent = text;
  else node.append(el("div", { class: "md typing" }));
  $("#chat").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function onCite(page) {
  const fig = state.figures.find(f => f.page === page);
  if (fig) { showAsset(fig); activatePane("diagram"); return; }
  const cite = state.citations.find(c => c.page === page && c.doc_id);
  if (cite) window.open(`/content-docs/${cite.doc_id}.pdf#page=${page}`, "_blank", "noopener");
}

async function* sseEvents(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (!ev || !data) continue;
      let parsed;
      try { parsed = JSON.parse(data); } catch { continue; }
      yield [ev, parsed];
    }
  }
}

function setBusy(busy) {
  state.busy = busy;
  $("#new-job").disabled = busy;
  $("#set-select").disabled = busy;
  $("#send").disabled = busy;
  $("#input").disabled = busy;
  $$(".chip").forEach(c => { c.disabled = busy; });
}

async function ask(message) {
  if (state.busy || !message.trim()) return;
  setBusy(true);
  activatePane("steps");
  addMessage("user", message);
  const bubble = addMessage("assistant");
  const md = $(".md", bubble);
  const firstTurn = !state.conversationId;
  if (firstTurn) toolsSkeleton();
  let text = "", frame = 0, errored = false, done = false;
  const paint = () => { frame = 0; md.innerHTML = markdown(text); bubble.scrollIntoView({ block: "end" }); };
  const fail = message => {
    errored = true;
    cancelAnimationFrame(frame);
    md.classList.remove("typing");
    bubble.classList.add("error");
    md.textContent = message;
  };
  try {
    const resp = await api("/api/chat", { set_id: state.setId, conversation_id: state.conversationId, message });
    if (resp.status === 401) { location.reload(); return; }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    for await (const [ev, data] of sseEvents(resp)) {
      if (ev === "meta") {
        state.conversationId = data.conversation_id;
        if (data.remaining_today !== null && state.me && !state.me.is_admin) updateQuota({ ...state.me, remaining: data.remaining_today });
      } else if (ev === "token") {
        md.classList.remove("typing");
        text += data.text;
        if (!frame) frame = requestAnimationFrame(paint);
      } else if (ev === "assets") {
        state.citations = data.citations;
        showFigures(data.figures);
      } else if (ev === "tools") {
        if (data.error) toolsError(); else renderTools(data.groups, data.figures);
      } else if (ev === "error") {
        fail(data.message);
        text = "";
        if (data.code === "quota_exceeded" && state.me) updateQuota({ ...state.me, remaining: 0 });
      } else if (ev === "done") {
        done = true;
      }
    }
  } catch (err) {
    if (!errored && !text) fail("Connection problem. Please try again.");
  } finally {
    if (!errored) {
      cancelAnimationFrame(frame);
      if (text) paint();
      md.classList.remove("typing");
      if (!done) {
        if (text) md.append(el("p", { class: "interrupted", text: "(answer interrupted — try again)" }));
        else fail("Connection problem. Please try again.");
      }
    }
    if ((errored || !done) && $("#tools .skeleton")) {
      if (state.conversationId && !errored) toolsError();
      else $("#tools").innerHTML = '<p class="empty">The tools for the job appear here after your first question.</p>';
    }
    setBusy(false);
    $("#input").focus();
  }
}

function newJob() {
  state.conversationId = null;
  state.figures = [];
  state.citations = [];
  $$("#chat .msg").forEach(m => m.remove());
  $("#starter").hidden = false;
  $("#tools").innerHTML = '<p class="empty">The tools for the job appear here after your first question.</p>';
  setView(state.view === "drawings" ? "drawings" : "figures");
}

function bindWorkspace() {
  $("#composer").addEventListener("submit", e => {
    e.preventDefault();
    const input = $("#input");
    const msg = input.value;
    input.value = "";
    input.style.height = "";
    ask(msg);
  });
  $("#input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#input").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; });
  $$(".chip").forEach(c => c.addEventListener("click", () => ask(c.textContent)));
  $("#chat").addEventListener("click", e => { const b = e.target.closest(".cite"); if (b) onCite(Number(b.dataset.page)); });
  $("#new-job").addEventListener("click", newJob);
  $$(".tabs [data-pane]").forEach(b => b.addEventListener("click", () => activatePane(b.dataset.pane)));
  $$(".seg [data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
  bindLightbox();
}

document.addEventListener("onesi:ready", bindWorkspace, { once: true });
document.addEventListener("onesi:set-changed", async () => { await loadDrawings(); newJob(); });
