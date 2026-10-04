const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let status = "pending";

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const resp = await fetch(path, opts);
  if (resp.status === 401 && path !== "/api/admin/login") { showLogin(); return resp; }
  if (!resp.ok && path !== "/api/admin/login") {
    let detail = null;
    try { const j = await resp.clone().json(); if (typeof j.detail === "string") detail = j.detail; } catch {}
    setMsg(detail || `Request failed (${resp.status})`, true);
  }
  return resp;
}

function setMsg(text, isError = false) {
  const m = $("#admin-msg");
  m.textContent = text || "";
  m.style.color = isError ? "#b42318" : "#067647";
}

function cell(text, cls) { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = text ?? ""; return td; }
function button(label, onClick, primary = false) {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = label; if (primary) b.className = "primary";
  b.addEventListener("click", onClick);
  return b;
}
const when = iso => (iso ? new Date(iso).toLocaleString() : "—");

async function act(email, action) {
  if (action === "revoke" && !confirm(`Revoke access for ${email}? Their sessions end immediately.`)) return;
  const resp = await api(`/api/admin/requests/${encodeURIComponent(email)}/${action}`, { method: "POST" });
  await refresh(!resp.ok);
  if (resp.ok && action === "resend-code") setMsg(`Code sent to ${email}.`);
}

async function showConversations(email) {
  const resp = await api(`/api/admin/requests/${encodeURIComponent(email)}/conversations`);
  if (!resp.ok) return;
  const convs = await resp.json();
  const body = $("#conv-body");
  body.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = `${email}: ${convs.length} ${convs.length === 1 ? "conversation" : "conversations"}`; body.append(h);
  for (const c of convs) for (const m of c.messages) {
    const div = document.createElement("div");
    div.className = "turn";
    div.textContent = `${m.role === "user" ? "Q" : "A"} · ${when(m.created_at)}\n${m.content}`;
    body.append(div);
  }
  $("#conv-dialog").showModal();
}

function renderRows(rows) {
  const head = $("#thead"), tbody = $("#rows");
  const cols = status === "approved" ? ["Who", "Today", "Total", "Last active", "Daily limit", ""] : ["Who", "Note", "Requested", ""];
  head.innerHTML = "";
  const tr = document.createElement("tr");
  cols.forEach(c => { const th = document.createElement("th"); th.textContent = c; tr.append(th); });
  head.append(tr);
  tbody.innerHTML = "";
  if (!rows.length) { const r = document.createElement("tr"); r.append(cell("Nothing here.")); tbody.append(r); return; }
  for (const r of rows) {
    const row = document.createElement("tr");
    const who = cell(`${r.name} · ${r.company}`);
    const email = document.createElement("div"); email.className = "note"; email.textContent = r.email; who.append(email);
    if (r.email_error) { const e = document.createElement("div"); e.className = "err"; e.textContent = r.email_error; who.append(e); }
    row.append(who);
    const actions = document.createElement("td");
    const box = document.createElement("div"); box.className = "actions"; actions.append(box);
    if (status === "pending") {
      row.append(cell(r.note, "note"), cell(when(r.created_at)));
      box.append(button("Approve", () => act(r.email, "approve"), true), button("Reject", () => act(r.email, "reject")));
    } else if (status === "approved") {
      const limit = document.createElement("input");
      limit.type = "number"; limit.min = "0"; limit.className = "limit"; limit.placeholder = "default";
      limit.value = r.daily_limit ?? "";
      limit.addEventListener("change", async () => {
        const resp = await api(`/api/admin/requests/${encodeURIComponent(r.email)}`, { method: "PATCH",
          body: { daily_limit: limit.value === "" ? null : Number(limit.value) } });
        await refresh(!resp.ok);
      });
      const limitCell = document.createElement("td"); limitCell.append(limit);
      row.append(cell(String(r.questions_today)), cell(String(r.questions_total)), cell(when(r.last_active)), limitCell);
      box.append(button("Conversations", () => showConversations(r.email)), button("Resend code", () => act(r.email, "resend-code")),
        button("Reset today", () => act(r.email, "reset-quota")), button("Revoke", () => act(r.email, "revoke")));
    } else {
      row.append(cell(r.status, "note"), cell(when(r.decided_at)));
      box.append(button("Restore to pending", () => act(r.email, "restore")));
    }
    row.append(actions);
    tbody.append(row);
  }
}

async function refresh(keepMsg = false) {
  const mine = status;
  if (!keepMsg) setMsg("");
  const [rowsResp, statsResp] = await Promise.all([api(`/api/admin/requests?status=${mine}`), api("/api/admin/stats")]);
  if (mine !== status) return;
  if (rowsResp.ok) renderRows(await rowsResp.json());
  if (statsResp.ok) {
    const s = await statsResp.json();
    $("#stats").innerHTML = "";
    for (const [value, label] of [[s.pending, "pending requests"], [`${s.questions_today} / ${s.global_limit}`, "questions today"],
      [`$${s.cost_today_usd.toFixed(2)}`, "estimated spend today"]]) {
      const d = document.createElement("div"); d.className = "stat";
      const b = document.createElement("b"); b.textContent = value;
      const span = document.createElement("span"); span.textContent = label;
      d.append(b, span); $("#stats").append(d);
    }
  }
}

function showLogin() { $("#login-view").hidden = false; $("#admin-view").hidden = true; $("#logout").hidden = true; }
function showAdmin() { $("#login-view").hidden = true; $("#admin-view").hidden = false; $("#logout").hidden = false; refresh(); }

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const resp = await api("/api/admin/login", { method: "POST", body: { password: form.password.value } });
  const err = $(".form-error", form);
  if (resp.ok) { err.hidden = true; showAdmin(); }
  else {
    let detail = null;
    try { const j = await resp.json(); if (typeof j.detail === "string") detail = j.detail; } catch {}
    err.textContent = detail || "Sign-in failed."; err.hidden = false;
  }
});
$("#logout").addEventListener("click", async () => { await api("/api/admin/logout", { method: "POST", body: {} }); showLogin(); });
$$("#status-tabs [data-status]").forEach(b => b.addEventListener("click", () => {
  status = b.dataset.status;
  $$("#status-tabs [data-status]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  refresh();
}));

(async () => { (await api("/api/admin/stats")).ok ? showAdmin() : showLogin(); })();
