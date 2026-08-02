"use strict";

/* ================= Core ================= */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let me = null;
let settings = null;
let currentSportFilter = "all";
let planWeek = null;
let currentSuggestion = null;

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("Server nicht erreichbar");
  }
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    showLogin();
    throw new Error("Nicht eingeloggt");
  }
  if (!res.ok) {
    let msg = (data && data.detail) || res.statusText;
    const err = new Error(typeof msg === "string" ? msg : (msg.error || JSON.stringify(msg)));
    err.mfa = !!(data && data.detail && data.detail.mfa);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(msg, cls = "") {
  const t = document.createElement("div");
  t.className = "toast " + cls;
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function fmtNum(v, digits = 1) {
  if (v === null || v === undefined) return "–";
  return String(Number(v).toFixed(digits)).replace(".", ",");
}

function sportLabel(sport) {
  return { running: "Laufen", cycling: "Radfahren", strength: "Kraft", rest: "Pause", other: "Sonstiges" }[sport] || sport;
}

function stepLabel(typ) {
  return { warmup: "Aufwärmen", interval: "Intervall", recovery: "Erholung", cooldown: "Auslaufen", rest: "Pause" }[typ] || typ;
}

function canSend(sport, steps) {
  return (sport === "running" || sport === "cycling") && Array.isArray(steps) && steps.length > 0;
}

function fmtLocal(iso, withDate = true) {
  if (!iso) return "–";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  if (isNaN(d)) return iso;
  const opts = withDate
    ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" };
  return d.toLocaleString("de-DE", opts);
}

function notify(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body, icon: "/icons/icon.svg" }); } catch (e) {}
  }
}

const ZONE_COLORS = ["z1", "z2", "z3", "z4", "z5"];
const WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

/* ================= Auth ================= */
function showLogin() {
  $("#appRoot").hidden = true;
  $("#loginScreen").hidden = false;
  $("#loginError").hidden = true;
}
function showApp() {
  $("#loginScreen").hidden = true;
  $("#appRoot").hidden = false;
}

async function initAuth() {
  try {
    const r = await api("/api/auth/me");
    me = r.user;
    showApp();
    renderUserChip();
    await refreshSettings();
    await loadDashboard();
    updateSuggBadge();
  } catch (e) {
    showLogin();
  }
}

function renderUserChip() {
  $("#userInitial").textContent = (me.display_name || me.username)[0].toUpperCase();
  $("#menuName").textContent = me.display_name || me.username;
  $("#menuUser").textContent = "@" + me.username + (me.is_admin ? " · Admin" : "");
}

$("#loginBtn").addEventListener("click", async () => {
  const u = $("#loginUsername").value.trim();
  const p = $("#loginPassword").value;
  if (!u || !p) return;
  const btn = $("#loginBtn");
  btn.disabled = true;
  btn.textContent = "Anmelden…";
  try {
    const r = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: u, password: p }),
    });
    me = r.user;
    showApp();
    renderUserChip();
    await refreshSettings();
    await loadDashboard();
    toast("Willkommen, " + (me.display_name || me.username) + "!", "ok");
  } catch (err) {
    $("#loginError").textContent = err.message;
    $("#loginError").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Anmelden";
  }
});

$("#loginPassword").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#loginBtn").click(); });

$("#logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  $("#userMenu").hidden = true;
  showLogin();
});

$("#userChip").addEventListener("click", (e) => {
  if (e.target.closest("#logoutBtn")) return;
  $("#userMenu").hidden = !$("#userMenu").hidden;
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".userchip")) $("#userMenu").hidden = true;
});

/* ================= Theme ================= */
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("theme", theme);
}
(function initTheme() {
  const saved = localStorage.getItem("theme") || "auto";
  applyTheme(saved);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (document.documentElement.dataset.theme === "auto") applyTheme("auto");
  });
})();
$("#themeToggle").addEventListener("click", () => {
  const order = ["auto", "dark", "light"];
  const cur = document.documentElement.dataset.theme;
  const next = order[(order.indexOf(cur) + 1) % order.length];
  applyTheme(next);
  const names = { auto: "Automatisch", dark: "Dunkel", light: "Hell" };
  toast("Design: " + names[next]);
});

/* ================= Navigation ================= */
$$(".bottomnav button").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

function switchView(name) {
  $$(".bottomnav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  if (name === "dashboard") loadDashboard();
  if (name === "plan") loadPlan();
  if (name === "activities") loadActivities();
  if (name === "suggestion") loadSuggestion();
  if (name === "settings") loadSettings();
}

/* ================= Sync badge ================= */
async function refreshSettings() {
  settings = await api("/api/settings");
  const badge = $("#syncBadge");
  const cls = settings.sync_status === "ok" ? "ok" : settings.sync_status === "error" ? "err" : settings.sync_status === "mfa" ? "warn" : "";
  badge.className = "sync-badge " + cls;
  badge.title = settings.sync_message || "";
  badge.textContent =
    settings.sync_status === "ok"
      ? "✓ " + fmtLocal(settings.last_sync, false)
      : settings.sync_status === "never" ? "Kein Sync" : settings.sync_status === "mfa" ? "2FA nötig" : "Sync-Fehler";
}

/* ================= Dashboard ================= */
async function loadDashboard() {
  const skel = $("#dashRings");
  skel.innerHTML = "";
  const [sum, zones] = await Promise.all([
    api("/api/stats/summary?days=7"),
    api("/api/stats/zones?days=30"),
  ]);
  renderRings(sum.totals);
  renderCards(sum.totals);
  renderBarChart(sum.series);
  renderZoneBar(zones.shares, zones.total_minutes);
}

function renderRings(t) {
  const rings = [
    { label: "Lauf", value: t.running_km, max: Math.max(t.running_km, 1), unit: "km", color: "var(--run)" },
    { label: "Rad", value: t.cycling_km, max: Math.max(t.cycling_km, 1), unit: "km", color: "var(--cycle)" },
    { label: "Einheiten", value: t.sessions, max: Math.max(t.sessions, 1), unit: "", color: "var(--strength)" },
  ];
  const box = $("#dashRings");
  box.innerHTML = "";
  const R = 40, C = 2 * Math.PI * R;
  rings.forEach((r) => {
    const frac = Math.min(1, r.value / r.max);
    const wrap = el("div", "ring-wrap");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.classList.add("ring-svg");
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    bg.setAttribute("cx", "50"); bg.setAttribute("cy", "50"); bg.setAttribute("r", R);
    bg.classList.add("ring-bg");
    const fg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    fg.setAttribute("cx", "50"); fg.setAttribute("cy", "50"); fg.setAttribute("r", R);
    fg.classList.add("ring-fg");
    fg.style.stroke = r.color;
    fg.style.strokeDasharray = C;
    fg.style.strokeDashoffset = C;
    svg.append(bg, fg);
    const val = el("div", "ring-value", fmtNum(r.value, r.unit === "" ? 0 : 1) + (r.unit ? " " + r.unit : ""));
    wrap.append(svg, val, el("div", "ring-label", r.label));
    box.append(wrap);
    requestAnimationFrame(() => { fg.style.strokeDashoffset = C * (1 - frac); });
  });
}

function renderCards(t) {
  $("#dashCards").innerHTML = "";
  const cards = [
    { label: "Lauf-km", value: fmtNum(t.running_km) + " km" },
    { label: "Rad-km", value: fmtNum(t.cycling_km) + " km" },
    { label: "Kraft", value: t.strength_count + "×" },
    { label: "Kalorien", value: fmtNum(t.calories, 0) },
    { label: "Schlaf Ø", value: t.avg_sleep_h !== null ? fmtNum(t.avg_sleep_h) + " h" : "–" },
    { label: "HF Ø", value: t.avg_hr ? Math.round(t.avg_hr) : "–" },
  ];
  cards.forEach((c, i) => {
    const d = el("div", "card");
    d.style.animationDelay = i * 0.05 + "s";
    d.append(el("div", "label", c.label), el("div", "value", c.value));
    $("#dashCards").append(d);
  });
}

function renderBarChart(series) {
  const box = $("#dashBarChart");
  box.innerHTML = "";
  const max = Math.max(1, ...series.map((s) => Math.max(s.running_km, s.cycling_km)));
  series.forEach((s, i) => {
    const col = el("div", "bar-col");
    const stack = el("div", "bar-stack");
    const runH = s.running_km > 0 ? Math.max(5, (s.running_km / max) * 100) : 0;
    const cycH = s.cycling_km > 0 ? Math.max(5, (s.cycling_km / max) * 100) : 0;
    const empty = el("div", "bar");
    empty.style.flexGrow = 1;
    if (runH > 0) { const b = el("div", "bar run"); b.style.height = runH + "%"; b.style.animationDelay = i * 0.06 + "s"; stack.append(b); }
    if (cycH > 0) { const b = el("div", "bar cycle"); b.style.height = cycH + "%"; b.style.animationDelay = i * 0.06 + "s"; stack.append(b); }
    if (runH === 0 && cycH === 0) stack.append(empty);
    const label = el("div", "bar-label", WEEKDAYS_SHORT[i]);
    col.append(stack, label);
    if (s.activities && s.activities.length) {
      const tooltip = buildDayTooltip(s, WEEKDAYS_SHORT[i]);
      col.append(tooltip);
      col.classList.add("has-tooltip");
      col.addEventListener("mouseenter", () => tooltip.classList.add("show"));
      col.addEventListener("mouseleave", () => tooltip.classList.remove("show"));
      col.addEventListener("click", () => {
        tooltip.classList.toggle("show");
      });
      label.classList.add("has-data");
    }
    box.append(col);
  });
}

function buildDayTooltip(s, weekday) {
  const tip = el("div", "chart-tooltip");
  const dateStr = new Date(s.date + "T12:00:00").toLocaleDateString("de-DE", {
    day: "2-digit", month: "2-digit",
  });
  tip.append(el("div", "tt-head", weekday + ", " + dateStr));
  tip.append(
    el(
      "div",
      "tt-summary",
      s.sessions + " Einheit" + (s.sessions !== 1 ? "en" : "") +
      " · " + fmtNum(s.calories, 0) + " kcal" +
      (s.sleep_h ? " · Schlaf " + fmtNum(s.sleep_h) + " h" : "")
    )
  );
  const list = el("div", "tt-list");
  s.activities.forEach((a) => {
    const row = el("div", "tt-row");
    const icon = a.sport === "running" ? "🏃" : a.sport === "cycling" ? "🚴" : a.sport === "strength" ? "🏋️" : "📋";
    const dist = a.distance_km ? fmtNum(a.distance_km) + " km" : null;
    const parts = [
      a.name || sportLabel(a.sport),
      dist,
      a.duration_min + " min",
      a.avg_hr ? "HF " + Math.round(a.avg_hr) : null,
    ].filter(Boolean);
    row.append(icon, " ");
    const text = el("span", "", parts.join(" · "));
    row.append(text);
    list.append(row);
  });
  tip.append(list);
  return tip;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".bar-col")) {
    $$(".chart-tooltip.show").forEach((t) => t.classList.remove("show"));
  }
});

function renderZoneBar(shares, minutes) {
  const bar = $("#dashZoneBar");
  bar.innerHTML = "";
  for (let i = 0; i < 5; i++) {
    const pct = shares["zone" + (i + 1)] || 0;
    const seg = el("div", "zone " + ZONE_COLORS[i]);
    seg.style.width = pct + "%";
    if (pct >= 11) seg.textContent = "Z" + (i + 1);
    bar.append(seg);
  }
  $("#dashZoneLegend").innerHTML = "";
  for (let i = 0; i < 5; i++) {
    const s = el("span");
    s.append(el("span", "dot " + ZONE_COLORS[i]), "Z" + (i + 1) + " " + fmtNum(shares["zone" + (i + 1)]) + "%");
    $("#dashZoneLegend").append(s);
  }
  $("#dashZoneTotal").textContent = minutes + " min gesamt";
}

/* ================= Plan ================= */
async function loadPlan(week) {
  const list = $("#planList");
  list.innerHTML = '<div class="skeleton" style="height:70px"></div>';
  const url = week ? `/api/plan?week=${encodeURIComponent(week)}` : "/api/plan";
  const data = await api(url);
  planWeek = data.week;
  $("#planWeek").textContent = data.week;
  list.innerHTML = "";
  if (!data.items.length) {
    list.append(el("p", "muted", "Noch kein Plan für diese Woche."));
    return;
  }
  data.items.forEach((d, i) => {
    const row = el("div", "plan-day" + (d.done ? " done" : ""));
    row.style.animationDelay = i * 0.04 + "s";
    const check = el("div", "check", "✓");
    check.addEventListener("click", () => togglePlanDay(d.id, row));
    const body = el("div", "plan-body");
    const meta = el("div", "plan-meta");
    meta.append(
      el("span", "sport-tag " + d.sport, d.sport === "rest" ? "Pause" : sportLabel(d.sport)),
      el("span", "", d.weekday)
    );
    body.append(meta, el("div", "plan-focus", d.focus || d.description || "Einheit"));
    if (d.description && d.description !== d.focus) body.append(el("div", "plan-desc", d.description));
    if (d.garmin_workout_id || canSend(d.sport, d.steps)) {
      const sr = el("div", "send-row");
      if (d.garmin_workout_id) {
        sr.append(el("span", "send-msg", "✓ Auf Garmin"));
        const del = el("button", "btn-mini del", "Entfernen");
        del.addEventListener("click", () => deleteGarminWorkout(d.garmin_workout_id, "plan"));
        sr.append(del);
      } else {
        const send = el("button", "btn-mini", "→ Gerät senden");
        send.addEventListener("click", () => sendPlanDayWithDevices(d, send));
        sr.append(send);
      }
      body.append(sr);
    }
    row.append(check, body);
    list.append(row);
  });
}

async function togglePlanDay(id, row) {
  const d = await api(`/api/plan/${id}/toggle`, { method: "POST" });
  row.classList.toggle("done", d.done);
}



async function deleteGarminWorkout(workoutId) {
  try {
    await api(`/api/garmin/workout/${encodeURIComponent(workoutId)}`, { method: "DELETE" });
    toast("Workout von Garmin entfernt", "ok");
    loadPlan(planWeek);
    loadSuggestion();
  } catch (err) {
    toast("Löschen fehlgeschlagen: " + err.message, "err");
  }
}

$("#planGenerate").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Generiere Plan… (1–2 Min)";
  try {
    const r = await api("/api/plan/generate", { method: "POST" });
    await loadPlan(r.week);
    toast("Plan für Woche " + r.week + " ist fertig", "ok");
    notify("Trainingsplan fertig", "Dein 7-Tage-Plan für Woche " + r.week + " ist bereit!");
  } catch (err) {
    toast("Plan-Generierung fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Plan generieren";
  }
});

$("#weekPrev").addEventListener("click", () => shiftPlanWeek(-1));
$("#weekNext").addEventListener("click", () => shiftPlanWeek(1));

$("#planSendAll").addEventListener("click", async (e) => {
  const btn = e.target;
  if (!planWeek) return loadPlan();
  if (!confirm("Alle sendbaren Tage der Woche " + planWeek + " als Workout an Garmin senden?")) return;
  btn.disabled = true;
  btn.textContent = "Sende…";
  try {
    const r = await api(`/api/garmin/workout/plan-all?week=${encodeURIComponent(planWeek)}`, { method: "POST" });
    const msg = [
      r.sent.length ? r.sent.length + " gesendet" : null,
      r.skipped.length ? r.skipped.length + " übersprungen" : null,
      r.errors.length ? r.errors.length + " Fehler" : null,
    ].filter(Boolean).join(", ");
    toast("Fertig: " + msg, r.errors.length ? "err" : "ok");
    if (r.sent.length) notify("Workouts gesendet", msg);
    loadPlan(planWeek);
  } catch (err) {
    toast("Senden fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Alle Tage an Garmin senden";
  }
});

function mondayOfIsoWeek(year, week) {
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const day = jan4.getUTCDay() || 7;
  return new Date(Date.UTC(year, 0, 4 - day + (week - 1) * 7));
}
function isoWeekOfDate(d) {
  const target = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((target - yearStart) / 86400000 + 1) / 7);
  return `${target.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}
function shiftPlanWeek(delta) {
  if (!planWeek) return loadPlan();
  const m = planWeek.match(/(\d{4})-W(\d{2})/);
  if (!m) return loadPlan();
  const monday = mondayOfIsoWeek(+m[1], +m[2]);
  monday.setUTCDate(monday.getUTCDate() + delta * 7);
  loadPlan(isoWeekOfDate(monday));
}

/* ================= Activities ================= */
async function loadActivities() {
  renderChips();
  const list = $("#activityList");
  list.innerHTML = '<div class="skeleton" style="height:70px;margin-bottom:8px"></div><div class="skeleton" style="height:70px"></div>';
  const data = await api("/api/activities?limit=200" + (currentSportFilter !== "all" ? "&sport=" + currentSportFilter : ""));
  list.innerHTML = "";
  if (!data.items.length) {
    list.append(el("p", "muted", "Noch keine Aktivitäten. Synchronisiere Garmin oder lade FIT-Dateien hoch."));
    return;
  }
  data.items.forEach((a, i) => {
    const row = el("div", "activity");
    row.style.animationDelay = Math.min(i * 0.03, 0.4) + "s";
    const main = el("div", "a-main");
    main.append(el("div", "a-name", a.name || sportLabel(a.sport)));
    const meta = el("div", "a-meta");
    const parts = [a.start_time.slice(0, 16).replace("T", " "), sportLabel(a.sport)];
    if (a.source === "fit") parts.push("FIT");
    if (a.duration_min) parts.push(a.duration_min + " min");
    meta.textContent = parts.join(" · ");
    main.append(meta);
    const stat = el("div", "a-stat");
    if (a.sport === "running" && a.distance_km !== null) {
      stat.append(el("b", "", fmtNum(a.distance_km) + " km"), "Ø " + paceFmt(a.avg_pace_min_km) + "/km");
    } else if (a.sport === "cycling" && a.distance_km !== null) {
      stat.append(el("b", "", fmtNum(a.distance_km) + " km"), "Ø " + fmtNum(a.avg_speed_kmh) + " km/h");
    } else {
      stat.append(el("b", "", (a.calories || 0) + " kcal"), a.avg_hr ? "Ø " + Math.round(a.avg_hr) + " HF" : "–");
    }
    row.append(main, stat);
    row.addEventListener("click", () => openActivitySheet(a));
    list.append(row);
  });
}

function renderChips() {
  const chips = $("#sportChips");
  chips.innerHTML = "";
  [["all", "Alle"], ["running", "Laufen"], ["cycling", "Radfahren"], ["strength", "Kraft"]].forEach(([v, label]) => {
    const c = el("div", "chip" + (currentSportFilter === v ? " active" : ""), label);
    c.addEventListener("click", () => { currentSportFilter = v; loadActivities(); });
    chips.append(c);
  });
}

function paceFmt(pace) {
  if (pace === null || pace === undefined) return "–";
  const min = Math.floor(pace);
  const sec = Math.round((pace - min) * 60);
  return min + ":" + String(sec).padStart(2, "0");
}

/* Bottom-Sheet */
function openActivitySheet(a) {
  const content = $("#sheetContent");
  content.innerHTML = "";
  content.append(el("h3", "", a.name || sportLabel(a.sport)));
  const rows = [
    ["Sportart", sportLabel(a.sport)],
    ["Datum", a.start_time.slice(0, 16).replace("T", " ")],
    ["Dauer", a.duration_min + " min"],
    ["Distanz", a.distance_km !== null ? fmtNum(a.distance_km) + " km" : "–"],
    ["Ø Puls", a.avg_hr ? Math.round(a.avg_hr) + " bpm" : "–"],
    ["Max Puls", a.max_hr ? Math.round(a.max_hr) + " bpm" : "–"],
    ["Kalorien", a.calories ? Math.round(a.calories) + " kcal" : "–"],
    ["Tempo", a.avg_pace_min_km ? paceFmt(a.avg_pace_min_km) + " /km" : a.avg_speed_kmh ? fmtNum(a.avg_speed_kmh) + " km/h" : "–"],
    ["Quelle", a.source === "fit" ? "FIT-Upload" : "Garmin"],
  ];
  rows.forEach(([k, v]) => {
    const row = el("div", "sheet-row");
    row.append(el("span", "sk", k), el("span", "sv", v));
    content.append(row);
  });
  $("#activitySheet").hidden = false;
  $("#sheetOverlay").hidden = false;
}
/* ---------- Geräte-Auswahl beim Workout-Senden ---------- */
const KIND_LABELS = {
  watch: "Uhr",
  bike_computer: "Radcomputer",
  hrm: "Herzfrequenzgurt",
  other: "Gerät",
};
let deviceSheetCallback = null;

async function openDeviceSheet(sport, onSend) {
  const overlay = $("#sheetOverlay");
  const sheet = $("#deviceSheet");
  const content = $("#deviceSheetContent");
  content.innerHTML = '<div class="skeleton" style="height:60px;margin:8px 0"></div>';
  overlay.hidden = false;
  sheet.hidden = false;
  try {
    const data = await api("/api/garmin/devices");
    const devices = data.items.filter((d) => d.kind !== "hrm");
    if (!devices.length) {
      content.innerHTML = '<p class="muted" style="padding:16px 4px">Keine Garmin-Geräte gefunden.</p>';
      return;
    }
    const preselected = new Set(
      sport === "cycling"
        ? devices.filter((d) => d.kind === "bike_computer").map((d) => d.device_id)
        : devices.filter((d) => d.kind === "watch").map((d) => d.device_id)
    );
    if (!preselected.size) devices.forEach((d) => preselected.add(d.device_id));

    const title = el("h3", "", "An welches Gerät senden?");
    const sub = el("p", "muted small", "Vorausgewählt passend zur Sportart (Lauf → Uhr, Rad → Radcomputer).");
    content.append(title, sub);
    devices.forEach((d) => {
      const row = el("label", "device-row");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = preselected.has(d.device_id);
      const info = el("div", "d-info");
      info.append(el("div", "d-name", d.name), el("div", "d-kind", KIND_LABELS[d.kind] || "Gerät"));
      row.append(cb, info);
      content.append(row);
    });
    const actions = el("div", "device-actions");
    const cancel = el("button", "btn", "Abbrechen");
    cancel.addEventListener("click", closeDeviceSheet);
    const send = el("button", "btn primary", "Senden");
    send.addEventListener("click", async () => {
      const ids = [...content.querySelectorAll("input[type=checkbox]")]
        .filter((c) => c.checked)
        .map((c) => c.closest(".device-row").dataset) ;
      const selected = devices
        .filter((d) => content.querySelectorAll("input[type=checkbox]")[devices.indexOf(d)].checked)
        .map((d) => d.device_id);
      if (!selected.length) return toast("Bitte mindestens ein Gerät wählen", "err");
      send.disabled = true;
      send.textContent = "Sende…";
      try {
        await onSend(selected);
        closeDeviceSheet();
      } catch (err) {
        toast("Senden fehlgeschlagen: " + err.message, "err");
        send.disabled = false;
        send.textContent = "Senden";
      }
    });
    actions.append(cancel, send);
    content.append(actions, el("div", "device-send-msg", "Das Workout erscheint nach dem Uhren-Sync auf dem Gerät."));
  } catch (err) {
    content.innerHTML = '<p class="muted" style="padding:16px 4px">Geräte laden fehlgeschlagen: ' + err.message + "</p>";
  }
}

function closeDeviceSheet() {
  $("#deviceSheet").hidden = true;
  $("#sheetOverlay").hidden = true;
  deviceSheetCallback = null;
}

async function sendSuggestionWithDevices(s, sendBtn) {
  openDeviceSheet(s.sport, async (deviceIds) => {
    const r = await api(`/api/garmin/workout/suggestion/${s.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_ids: deviceIds }),
    });
    const pushed = r.pushed && r.pushed.length ? r.pushed.filter((p) => p.ok).length + " Geräte" : "";
    toast("Workout gesendet" + (pushed ? " an " + pushed : ""), "ok");
    notify("Workout gesendet", r.name + " liegt jetzt auf Garmin");
    loadSuggestion();
  });
}

async function sendPlanDayWithDevices(d, btn) {
  openDeviceSheet(d.sport, async (deviceIds) => {
    const r = await api(`/api/garmin/workout/plan/${d.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_ids: deviceIds }),
    });
    const pushed = r.pushed && r.pushed.length ? r.pushed.filter((p) => p.ok).length + " Geräte" : "";
    toast("Workout gesendet" + (pushed ? " an " + pushed : ""), "ok");
    notify("Workout gesendet", r.name + " liegt jetzt auf Garmin");
    loadPlan(planWeek);
  });
}

$("#sheetOverlay").addEventListener("click", () => {
  closeSheet();
  closeDeviceSheet();
});
function closeSheet() {
  $("#activitySheet").hidden = true;
  $("#sheetOverlay").hidden = true;
}

$("#fitUpload").addEventListener("click", async () => {
  const files = $("#fitFiles").files;
  if (!files.length) return toast("Bitte FIT-Dateien wählen", "err");
  const fd = new FormData();
  [...files].forEach((f) => fd.append("files", f));
  $("#uploadResult").textContent = "Lade hoch…";
  try {
    const r = await api("/api/upload/fit", { method: "POST", body: fd });
    const ok = r.results.filter((x) => x.imported).length;
    const errs = r.results.filter((x) => x.error).length;
    toast(`${ok} importiert, ${r.results.length - ok - errs} übersprungen` + (errs ? `, ${errs} Fehler` : ""), errs ? "err" : "ok");
    loadActivities();
  } catch (err) {
    $("#uploadResult").textContent = "Upload fehlgeschlagen: " + err.message;
  }
});

/* ================= Suggestion ================= */
async function loadSuggestion() {
  const box = $("#suggestionBox");
  box.innerHTML = '<div class="skeleton" style="height:120px"></div>';
  const data = await api("/api/suggestion");
  box.innerHTML = "";
  if (!data.ok) {
    box.append(el("p", "muted", data.message));
    return;
  }
  const s = data.suggestion;
  currentSuggestion = s;
  const card = el("div", "suggestion");
  card.append(el("h3", "", s.title || "Heutiges Training"));
  card.append(el("div", "s-label", "Begründung"));
  card.append(el("div", "s-text", s.rationale || "–"));
  card.append(el("div", "s-label", "Training"));
  card.append(el("div", "s-text", s.workout || "–"));
  if (s.steps && s.steps.length) {
    card.append(el("div", "s-label", "Schritte"));
    const steps = el("div", "s-steps");
    s.steps.forEach((st) => steps.append(el("span", "s-step", stepLabel(st.typ) + " · " + st.dauer_min + " min" + (st.zone ? " · Z" + st.zone : ""))));
    card.append(steps);
  }
  card.append(el("div", "s-time", "Erstellt am " + fmtLocal(s.created_at)));
  if (s.garmin_workout_id) {
    const sr = el("div", "send-row");
    sr.append(el("span", "send-msg", "✓ Auf Garmin gesendet"));
    const del = el("button", "btn-mini del", "Entfernen");
    del.addEventListener("click", () => deleteGarminWorkout(s.garmin_workout_id));
    sr.append(del);
    card.append(sr);
  } else if (canSend(s.sport, s.steps)) {
    const sr = el("div", "send-row");
    const send = el("button", "btn-mini", "→ Gerät senden");
    send.addEventListener("click", () => sendSuggestionWithDevices(s, send));
    sr.append(send);
    card.append(sr);
  } else {
    card.append(el("div", "send-row", ""), (() => {
      const sr = el("div", "send-row");
      sr.append(el("span", "send-msg", "Ohne strukturierte Schritte kann kein Garmin-Workout erstellt werden – einfach neu generieren."));
      return sr;
    })());
  }
  box.append(card);
}

$("#suggGenerate").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Analysiere… (1–2 Min)";
  try {
    const r = await api("/api/suggestion/generate", { method: "POST" });
    await loadSuggestion();
    toast("Vorschlag erstellt: " + r.suggestion.title, "ok");
    notify("Trainingsvorschlag", r.suggestion.title);
  } catch (err) {
    toast("Vorschlag fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Vorschlag generieren";
  }
});

function updateSuggBadge() {
  // Badge: neuer Vorschlag seit letztem Besuch dieser Ansicht
}

/* ================= Settings ================= */
async function loadSettings() {
  await refreshSettings();
  const s = settings;
  $("#garminStatus").textContent = s.garmin_email
    ? "Verbunden als " + s.garmin_email
    : "Noch nicht verbunden – E-Mail und Passwort eingeben, alles läuft über diese Website.";

  const st = $("#settingsStatus");
  st.innerHTML = "";
  const rows = [
    ["Garmin", s.garmin_configured ? (s.sync_status === "ok" ? "Verbunden" : s.sync_status === "mfa" ? "2FA nötig" : "Konfiguriert") : "Nicht verbunden", s.garmin_configured ? (s.sync_status === "ok" ? "ok" : "warn") : "err"],
    ["Letzter Sync", s.last_sync ? fmtLocal(s.last_sync) + (s.sync_stale ? " (veraltet)" : "") : "Nie", s.sync_stale ? "warn" : "ok"],
    ["LLM Provider", s.llm.provider + (s.llm.model ? " · " + s.llm.model : ""), s.llm.configured ? "ok" : "err"],
  ];
  rows.forEach(([k, v, cls]) => {
    const row = el("div", "status-item");
    row.append(el("span", "s-key", k), el("span", "s-val " + cls, v));
    st.append(row);
  });
  if (s.sync_status === "mfa") $("#mfaBox").hidden = false;

  /* LLM-Formular */
  $("#llmStatus").textContent = s.llm.configured
    ? "Aktuell: " + s.llm.provider + (s.llm.model ? " · " + s.llm.model : "") + " (bereit)"
    : s.llm.error || "Nicht konfiguriert";
  if (me.llm_provider) $("#llmProvider").value = me.llm_provider;

  /* Admin-Bereich */
  $("#adminPanel").hidden = !me.is_admin;
  if (me.is_admin) loadUserList();
}

async function loadUserList() {
  const data = await api("/api/users");
  const list = $("#userList");
  list.innerHTML = "";
  data.items.forEach((u) => {
    const row = el("div", "user-row");
    const av = el("div", "u-avatar", (u.display_name || u.username)[0].toUpperCase());
    const info = el("div", "u-info");
    info.append(el("div", "u-name", u.display_name + (u.is_admin ? " (Admin)" : "")), el("div", "u-sub", "@" + u.username));
    const actions = el("div", "u-actions");
    if (!u.is_admin) {
      const del = el("button", "btn-mini del", "Löschen");
      del.addEventListener("click", async () => {
        if (!confirm("Benutzer " + u.username + " wirklich löschen?")) return;
        await api("/api/users/" + u.id, { method: "DELETE" });
        toast("Benutzer gelöscht", "ok");
        loadUserList();
      });
      actions.append(del);
    }
    row.append(av, info, actions);
    list.append(row);
  });
}

$("#garminSave").addEventListener("click", async (e) => {
  const btn = e.target;
  const email = $("#garminEmail").value.trim();
  const password = $("#garminPassword").value;
  if (!email || !password) return toast("Bitte E-Mail und Passwort eingeben", "err");
  btn.disabled = true;
  btn.textContent = "Speichere…";
  try {
    await api("/api/garmin/credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    $("#garminPassword").value = "";
    toast("Garmin-Login gespeichert – jetzt synchronisieren", "ok");
    loadSettings();
  } catch (err) {
    toast("Speichern fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Login speichern";
  }
});

async function doSync(mfaCode) {
  const btn = $("#syncNow");
  btn.disabled = true;
  btn.textContent = "Synchronisiere…";
  try {
    const r = await api("/api/garmin/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mfaCode ? { mfa_code: mfaCode } : {}),
    });
    $("#mfaBox").hidden = true;
    toast("Sync fertig: " + r.imported + " neu, " + r.skipped + " übersprungen", "ok");
    loadSettings();
  } catch (err) {
    if (err.mfa) {
      $("#mfaBox").hidden = false;
      $("#mfaCode").focus();
    } else {
      toast("Sync fehlgeschlagen: " + err.message, "err");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Jetzt synchronisieren";
  }
}
$("#syncNow").addEventListener("click", () => doSync(null));
$("#mfaSubmit").addEventListener("click", async () => {
  const code = $("#mfaCode").value.trim();
  if (!code) return toast("Bitte Code eingeben", "err");
  await doSync(code);
});

$("#llmSave").addEventListener("click", async (e) => {
  const btn = e.target;
  btn.disabled = true;
  btn.textContent = "Speichere…";
  try {
    const body = {
      provider: $("#llmProvider").value || null,
      base_url: $("#llmBase").value.trim() || null,
      model: $("#llmModel").value.trim() || null,
      api_key: $("#llmKey").value,
    };
    await api("/api/users/me/llm", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("#llmKey").value = "";
    toast("LLM-Konfiguration gespeichert", "ok");
    loadSettings();
  } catch (err) {
    toast("Speichern fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "LLM-Konfiguration speichern";
  }
});

$("#createUserBtn").addEventListener("click", async (e) => {
  const btn = e.target;
  const username = $("#newUserName").value.trim();
  const password = $("#newUserPass").value;
  const display = $("#newUserDisplay").value.trim();
  if (!username || !password) return toast("Benutzername und Passwort angeben", "err");
  btn.disabled = true;
  btn.textContent = "Lege an…";
  try {
    const r = await api("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name: display }),
    });
    $("#newUserName").value = "";
    $("#newUserPass").value = "";
    $("#newUserDisplay").value = "";
    toast("Benutzer " + r.user.username + " angelegt", "ok");
    loadUserList();
  } catch (err) {
    toast("Anlegen fehlgeschlagen: " + err.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "Benutzer anlegen";
  }
});

/* ================= Pull-to-Refresh ================= */
let pullStart = 0;
document.addEventListener("touchstart", (e) => {
  if (window.scrollY <= 0) pullStart = e.touches[0].clientY;
}, { passive: true });
document.addEventListener("touchend", (e) => {
  if (pullStart && e.changedTouches[0].clientY - pullStart > 90 && window.scrollY <= 0) {
    pullStart = 0;
    const active = document.querySelector(".view.active");
    if (active && active.id === "view-dashboard") {
      loadDashboard().then(() => toast("Aktualisiert", "ok"));
    }
  }
}, { passive: true });

/* ================= Init ================= */
(async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
  await initAuth();
})();
