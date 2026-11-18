"use strict";
// dashboard script, plain js and no libraries
// polls the api and only redraws what changed
const POLL_MS = 1500;
const $ = (id) => document.getElementById(id);
const state = { config: {}, bins: [], selected: null, pending: {}, notes: {}, sig: {}, seen: {}, readings: [], busy: false, firstLoad: true };

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v !== null && v !== undefined) node.setAttribute(k, v);
  });
  kids.flat().forEach((k) => node.append(k instanceof Node ? k : document.createTextNode(String(k))));
  return node;
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  const token = sessionStorage.getItem("adminToken");
  if (token) headers["X-Admin-Token"] = token;
  const res = await fetch(path, Object.assign({}, options, { headers }));
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* not json */ }
    const err = new Error(detail); err.status = res.status; throw err;
  }
  return res.json();
}

// formatting
const statusLabel = { OK: "OK", NEARLY_FULL: "Nearly full", FULL: "Full", OFFLINE: "Offline", SENSOR_FAULT: "Sensor fault", NO_DATA: "No data" };
const badgeOf = { OK: "green", NEARLY_FULL: "amber", FULL: "red", OFFLINE: "grey", SENSOR_FAULT: "red", NO_DATA: "grey" };
const triggerLabel = { lid_close: "Lid closed", refresh: "Refresh", heartbeat: "Heartbeat" };
const kindLabel = { nearly_full: "Nearly full", full: "Full", sensor_fault: "Sensor fault", offline: "Offline" };
const clock = (ts) => (ts ? new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–");
function ago(ts) {
  if (!ts) return "–";
  const s = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
const agoSpan = (ts) => el("span", { class: "ago", "data-ts": ts || "" }, ago(ts));
const tickAgo = () => document.querySelectorAll(".ago").forEach((n) => { n.textContent = ago(n.dataset.ts); });

// only redraw when data changed
function draw(key, data, render) {
  const s = JSON.stringify(data);
  if (state.sig[key] === s) return false;
  state.sig[key] = s; render(); return true;
}

// sections
function renderKpis(s) {
  const cards = [
    ["Bins monitored", s.bins, "reporting to the cloud", ""],
    ["Need collection", s.need_collection, "full, nearly full or filling fast", ""],
    ["Active alerts", s.active_alerts, `${s.collections_detected} collection(s) detected`, s.active_alerts ? "alert" : ""],
    ["Messages saved", `${s.saved_pct}%`, `${s.readings_sent} sent instead of ${s.polling_equivalent} (measuring every ${s.poll_baseline_min} min)`, ""],
  ];
  $("kpis").replaceChildren(...cards.map(([l, v, n, c]) =>
    el("div", { class: `kpi ${c}` }, el("div", { class: "l" }, l), el("div", { class: "v" }, v), el("div", { class: "n" }, n))));
}

async function requestRefresh(binId) {
  try {
    const r = await api(`/api/bins/${encodeURIComponent(binId)}/refresh`, { method: "POST" });
    state.pending[binId] = { id: r.command_id };
  } catch (e) {
    state.notes[binId] = { text: e.message, until: Date.now() + 6000 };
  }
  refresh();
}

async function pollCommands() {
  for (const binId of Object.keys(state.pending)) {
    try {
      const c = await api(`/api/commands/${state.pending[binId].id}`);
      if (c.status === "done") {
        delete state.pending[binId];
        state.notes[binId] = { text: c.fill_pct === null ? "Sensor error" : `Measured: ${c.fill_pct}%`, until: Date.now() + 6000 };
      } else if (c.status === "expired") {
        delete state.pending[binId];
        state.notes[binId] = { text: "No answer from device", until: Date.now() + 6000 };
      }
    } catch (e) { delete state.pending[binId]; }
  }
}

function binCard(b, flash) {
  const waiting = !!state.pending[b.id] || !!b.pending_command;
  const n = state.notes[b.id];
  const note = waiting ? "Measuring…" : (n && n.until > Date.now() ? n.text : "");
  const latest = b.latest;
  const faulty = b.status === "SENSOR_FAULT";
  const pct = b.fill_pct === null ? null : Math.round(b.fill_pct);
  const btn = el("button", { class: "btn", disabled: waiting, onclick: (ev) => { ev.stopPropagation(); requestRefresh(b.id); } }, "Refresh");
  const fill = el("div", { class: "fill" }); fill.style.width = `${pct === null ? 0 : pct}%`;
  let line1 = "No readings yet";
  if (latest) line1 = faulty ? `Reading out of range: ${latest.distance_cm} cm` : `${latest.distance_cm} cm from sensor · ${triggerLabel[latest.trigger] || latest.trigger}`;
  return el("div", { class: `bin s-${b.status}${b.id === state.selected ? " selected" : ""}${flash ? " flash" : ""}`, onclick: () => { state.selected = b.id; refresh(); } },
    el("div", { class: "head" },
      el("div", {}, el("div", { class: "name" }, b.name), el("div", { class: "loc" }, b.location && b.location !== b.name ? `${b.location} · ${b.id}` : b.id)),
      el("span", { class: `badge ${badgeOf[b.status]}` }, statusLabel[b.status])),
    el("div", { class: "pct" }, pct === null ? "–" : pct, pct === null ? "" : el("small", {}, "%")),
    el("div", { class: "track" }, fill),
    el("div", { class: `meta${faulty ? " bad" : ""}` }, line1),
    el("div", { class: "meta" }, latest ? el("span", {}, "Updated ", agoSpan(latest.received_at)) : "",
      b.hours_to_full !== null ? ` · full in ~${b.hours_to_full} h` : ""),
    el("div", { class: "foot" }, el("span", { class: "note" }, note), btn));
}

function renderBins(bins) {
  if (!bins.length) {
    $("bins").replaceChildren(el("div", { class: "panel empty" }, "No bins yet. A bin appears here automatically when its device sends the first reading."));
    return;
  }
  $("bins").replaceChildren(...bins.map((b) => {
    const stamp = b.latest ? b.latest.received_at : null;
    const flash = !state.firstLoad && state.seen[b.id] !== undefined && state.seen[b.id] !== stamp;
    state.seen[b.id] = stamp;
    return binCard(b, flash);
  }));
}

function renderRoute(p) {
  const list = $("route");
  if (!p.collect.length) list.replaceChildren(el("li", { class: "empty" }, "Nothing needs collecting right now."));
  else list.replaceChildren(...p.collect.map((r) =>
    el("li", {}, el("span", { class: "nm" }, r.name), el("span", { class: `badge ${badgeOf[r.status]}` }, statusLabel[r.status]),
      el("div", { class: "why" }, `${r.location ? r.location + " · " : ""}${r.reason}`))));
  $("checkBox").classList.toggle("hidden", !p.check_device.length);
  $("check").replaceChildren(...p.check_device.map((c) => el("li", {}, `${c.name}: ${statusLabel[c.status]}`)));
}

function actionButton(text, path) {
  return el("button", { class: "btn", onclick: async () => {
    try { await api(path, { method: "POST" }); await refresh(); } catch (e) { alert(`Action failed: ${e.message}`); }
  } }, text);
}

function renderAlerts(list) {
  const body = $("alerts").querySelector("tbody");
  if (!list.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: 5, class: "empty" }, "No alerts."))); return; }
  body.replaceChildren(...list.map((a) => {
    const active = !a.resolved_at, actions = el("td", { class: "actions" });
    if (active && a.status === "open") actions.append(actionButton("Acknowledge", `/api/alerts/${a.id}/ack`));
    if (active) actions.append(actionButton("Close", `/api/alerts/${a.id}/resolve`));
    const st = a.status === "resolved" ? `Resolved (${a.resolution})` : a.status === "acknowledged" ? "Acknowledged" : "Open";
    return el("tr", { title: a.message || "" }, el("td", {}, a.bin_name || a.bin_id), el("td", {}, kindLabel[a.kind] || a.kind),
      el("td", {}, clock(a.started_at)), el("td", {}, el("span", { class: `badge ${active ? "red" : "green"}` }, st)), actions);
  }));
}

function renderReadings(rows) {
  const body = $("readings").querySelector("tbody");
  if (!rows.length) { body.replaceChildren(el("tr", {}, el("td", { colspan: 4, class: "empty" }, "No readings yet."))); return; }
  body.replaceChildren(...rows.slice(-8).reverse().map((r) =>
    el("tr", {}, el("td", {}, clock(r.ts)), el("td", {}, (triggerLabel[r.trigger] || r.trigger) + (r.collected ? " · emptied" : "")),
      el("td", { class: "num" }, `${r.distance_cm} cm`), el("td", { class: "num" }, r.fill_pct === null ? "fault" : `${Math.round(r.fill_pct)}%`))));
}

function drawChart(rows) {
  const canvas = $("chart"), dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr); ctx.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement), v = (n) => css.getPropertyValue(n).trim();
  ctx.font = "11px system-ui, sans-serif"; ctx.fillStyle = v("--muted");
  const pts = rows.filter((r) => r.fill_pct !== null);
  if (pts.length < 2) { ctx.fillText("Waiting for at least two readings…", 10, 20); return; }
  const left = 34, right = 10, top = 8, bottom = 20;
  const t0 = new Date(pts[0].ts).getTime(), t1 = new Date(pts[pts.length - 1].ts).getTime();
  const x = (ts) => left + ((new Date(ts).getTime() - t0) / Math.max(1, t1 - t0)) * (w - left - right);
  const y = (p) => top + (1 - p / 100) * (h - top - bottom);
  ctx.strokeStyle = v("--line"); ctx.lineWidth = 1;
  [0, 25, 50, 75, 100].forEach((p) => { ctx.beginPath(); ctx.moveTo(left, y(p)); ctx.lineTo(w - right, y(p)); ctx.stroke(); ctx.fillText(`${p}%`, 2, y(p) + 4); });
  [[state.config.alert_pct, "--near"], [state.config.full_pct, "--full"]].forEach(([p, col]) => {
    if (p === undefined) return;
    ctx.setLineDash([5, 4]); ctx.strokeStyle = v(col); ctx.beginPath(); ctx.moveTo(left, y(p)); ctx.lineTo(w - right, y(p)); ctx.stroke();
  });
  ctx.setLineDash([]); ctx.strokeStyle = v("--muted");
  pts.filter((r) => r.collected).forEach((r) => { ctx.beginPath(); ctx.moveTo(x(r.ts), top); ctx.lineTo(x(r.ts), h - bottom); ctx.stroke(); });
  ctx.strokeStyle = v("--accent"); ctx.lineWidth = 1.8; ctx.beginPath();
  pts.forEach((r, i) => (i ? ctx.lineTo(x(r.ts), y(r.fill_pct)) : ctx.moveTo(x(r.ts), y(r.fill_pct))));
  ctx.stroke();
  const colour = { lid_close: "--accent", refresh: "--orange", heartbeat: "--off" };
  pts.forEach((r) => { ctx.fillStyle = v(colour[r.trigger] || "--accent"); ctx.beginPath(); ctx.arc(x(r.ts), y(r.fill_pct), 3, 0, 7); ctx.fill(); });
  ctx.fillStyle = v("--muted");
  ctx.fillText(clock(pts[0].ts), left, h - 4); ctx.fillText(clock(pts[pts.length - 1].ts), w - right - 52, h - 4);
}

// main loop
function setConn(kind, text) {
  $("conn").className = `live ${kind}`;
  $("connText").textContent = text;
}

async function refresh() {
  if (state.busy) return;
  state.busy = true;
  try {
    await pollCommands();
    const [summary, bins, route, alerts] = await Promise.all([api("/api/summary"), api("/api/bins"), api("/api/priority"), api("/api/alerts?limit=30")]);
    state.bins = bins;
    if (!state.selected || !bins.some((b) => b.id === state.selected)) {
      const hot = bins.find((b) => b.status === "FULL" || b.status === "NEARLY_FULL");
      state.selected = (hot || bins[0] || {}).id || null;
    }
    draw("kpis", summary, () => renderKpis(summary));
    const noteState = bins.map((b) => [b.id, (state.pending[b.id] || b.pending_command) ? 1 : 0,
      state.notes[b.id] && state.notes[b.id].until > Date.now() ? state.notes[b.id].text : ""]);
    draw("bins", [bins, state.selected, noteState], () => renderBins(bins));
    draw("route", route, () => renderRoute(route));
    draw("alerts", alerts, () => renderAlerts(alerts));
    const b = bins.find((x) => x.id === state.selected);
    $("detailTitle").textContent = b ? `Fill history · ${b.name}` : "Fill history";
    state.readings = b ? await api(`/api/bins/${encodeURIComponent(b.id)}/readings?limit=200`) : [];
    draw("chart", [state.selected, state.readings], () => drawChart(state.readings));
    draw("readings", [state.selected, state.readings.slice(-8)], () => renderReadings(state.readings));
    state.firstLoad = false;
    setConn("on", `Live · updated ${clock(new Date())}`);
  } catch (e) {
    const locked = e.status === 401;
    setConn("err", locked ? "Enter the admin token" : "Connection lost, retrying…");
  } finally {
    state.busy = false;
  }
}

async function setup() {
  try {
    state.config = await api("/api/config");
  } catch (e) { /* config is optional */ }
  $("tokenBox").classList.toggle("hidden", !(state.config.admin_required || state.config.dashboard_protected));
  $("token").value = sessionStorage.getItem("adminToken") || "";
  $("token").addEventListener("input", (e) => { sessionStorage.setItem("adminToken", e.target.value); refresh(); });
}

setup().finally(() => {
  refresh();
  setInterval(refresh, POLL_MS);
  setInterval(tickAgo, 1000);
});
window.addEventListener("resize", () => { state.sig.chart = null; drawChart(state.readings); });
