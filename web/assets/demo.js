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
