/* Agora dashboard — vanilla JS, no build step.
 * All agent-generated text is inserted with textContent (never innerHTML) because it is untrusted LLM output. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") Object.assign(n.style, v);
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const S = {
  meta: null, status: "idle", runId: null, cfg: null, envKey: null, steps: 0, step: 0,
  agents: new Map(), metrics: [], highlights: [], lots: [], feedCount: 0, viewingSetup: true,
  filters: { thoughts: true, public: true, private: true, deals: true, moves: false, conflict: true, alerts: true, system: false },
};

/* ================================================================ categories */
const CAT = {
  thought: "thoughts", say: "public", dm: "private", syndicate_msg: "private",
  trade: "deals", contract_settled: "deals", contract_accepted: "deals", lot_closed: "deals",
  order_posted: "moves", bid: "moves", harvest: "moves", craft: "moves", vote: "moves", rule_proposed: "moves",
  contract_proposed: "moves", restock: "moves", syndicate_formed: "moves", syndicate_joined: "moves", syndicate_left: "moves",
  syndicate_invite: "moves", consign: "moves",
  contract_reneged: "conflict", contract_defaulted: "conflict", accusation: "conflict", shill_detected: "conflict", audit_fine: "conflict",
  highlight: "alerts", summary: "alerts", rule_enacted: "alerts", rule_failed: "alerts", stock_collapse: "alerts",
  sim_started: "alerts", sim_completed: "alerts", sim_failed: "alerts",
};
const FILTER_LABELS = { thoughts: "Thoughts", public: "Public", private: "Private", deals: "Deals", moves: "Moves", conflict: "Conflict", alerts: "Alerts", system: "System" };
const HIDDEN_KINDS = new Set(["metrics", "step_started", "step_completed", "production", "regrowth", "shill_bid"]);

/* ================================================================ API */
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null;
  try { body = await r.json(); } catch (_) { /* non-JSON */ }
  if (!r.ok) {
    const d = body && body.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${(e.loc || []).join(".")}: ${e.msg}`).join("; ") : (d || `HTTP ${r.status}`);
    throw new Error(msg);
  }
  return body;
}

/* ================================================================ setup */
function buildSetup() {
  const m = S.meta;
  const envBox = $("#envChoices");
  envBox.replaceChildren(...m.environments.map((e, i) =>
    el("label", { class: "env-choice" },
      el("input", { type: "radio", name: "environment", value: e.key, checked: i === 0 }),
      el("b", { text: e.title }), el("span", { text: e.blurb }))));
  const mix = $("#mix");
  mix.replaceChildren(...m.archetypes.map((a) => {
    const out = el("output", { text: a.default_weight });
    const input = el("input", { type: "range", min: "0", max: "5", step: "0.5", value: a.default_weight, "data-arch": a.key, "aria-label": a.label + " weight",
      oninput: (e) => { out.textContent = e.target.value; } });
    return el("label", { title: a.essence }, el("i", { class: "dot", style: { background: a.color } }), el("span", { text: a.label }), input, out);
  }));
  const agents = $("input[name=num_agents]");
  agents.max = m.max_agents;
  agents.addEventListener("input", () => { $("#agentsOut").textContent = agents.value; });
  const syncEnv = () => {
    const env = $("input[name=environment]:checked").value;
    $$(".rules [data-env]").forEach((d) => d.classList.toggle("off", d.dataset.env !== env));
  };
  $$("input[name=environment]").forEach((r) => r.addEventListener("change", syncEnv));
  syncEnv();
  const syncBackend = () => {
    const b = $("input[name=backend]:checked").value;
    $("#ollamaFields").hidden = b !== "ollama";
    $("#heuristicHint").hidden = b === "ollama";
  };
  $$("input[name=backend]").forEach((r) => r.addEventListener("change", syncBackend));
  syncBackend();
  $("#btnCheck").addEventListener("click", checkOllama);
  $("#setupForm").addEventListener("submit", startSim);
}

async function checkOllama() {
  const msg = $("#ollamaMsg");
  msg.textContent = "Checking…";
  try {
    const h = await api(`/api/ollama?base_url=${encodeURIComponent($("input[name=base_url]").value)}`);
    if (!h.ok) { msg.textContent = h.error; return; }
    const sel = $("#modelSelect");
    const current = sel.value;
    if (h.models.length) {
      sel.replaceChildren(...h.models.map((name) => el("option", { value: name, text: name, selected: name === current })));
      msg.textContent = `Connected to Ollama ${h.version}. ${h.models.length} model(s) installed.`;
    } else {
      msg.textContent = `Connected to Ollama ${h.version}, but no models are installed. Run “ollama pull llama3.1:8b” or tick “pull missing models”.`;
    }
  } catch (e) { msg.textContent = `Could not check: ${e.message}`; }
}

function readConfig() {
  const f = $("#setupForm");
  const val = (n) => f.elements[n];
  const num = (n) => Number(val(n).value);
  const cfg = {
    name: val("name").value.trim() || "untitled experiment",
    environment: $("input[name=environment]:checked").value,
    num_agents: num("num_agents"), steps: num("steps"), seed: num("seed"),
    summary_every: num("summary_every"), step_delay_s: num("step_delay_s"),
    archetype_mix: {}, llm: {}, mechanism: {}, marketplace: {}, auction: {}, commons: {},
  };
  $$("#mix input").forEach((i) => { cfg.archetype_mix[i.dataset.arch] = Number(i.value); });
  cfg.llm.backend = $("input[name=backend]:checked").value;
  cfg.llm.base_url = val("base_url").value.trim();
  cfg.llm.model = val("model").value;
  cfg.llm.model_pool = val("model_pool").value.split(",").map((s) => s.trim()).filter(Boolean);
  cfg.llm.max_concurrency = num("max_concurrency");
  cfg.llm.auto_pull = val("auto_pull").checked;
  $$("#setupForm [name*='.']").forEach((i) => {
    const [grp, key] = i.name.split(".");
    cfg[grp][key] = i.type === "checkbox" ? i.checked : (i.type === "number" ? Number(i.value) : i.value);
  });
  return cfg;
}

async function startSim(ev) {
  ev.preventDefault();
  const err = $("#formError");
  err.hidden = true;
  const btn = $("#btnStart");
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    await api("/api/sim/start", { method: "POST", body: JSON.stringify(readConfig()) });
    S.viewingSetup = false;
  } catch (e) {
    err.replaceChildren(e.message);
    if (isActive(S.status)) {
      err.append(" ", el("button", { type: "button", class: "ghost small", text: "Watch the running simulation", onclick: () => { S.viewingSetup = false; showLive(); } }));
    }
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Start simulation";
  }
}

async function loadPastRuns() {
  try {
    const { runs } = await api("/api/runs");
    const box = $("#pastRuns");
    if (!runs.length) return;
    box.replaceChildren(...runs.slice(0, 12).map((r) => el("div", { class: "past-item" },
      el("span", {}, el("b", { text: r.name || r.run_id }), " ", el("span", { class: "hint", text: `${r.environment} · ${r.agents} agents · ${r.steps_completed} steps · ${r.status}` })),
      el("span", { class: "hint", text: `Gini ${r.gini_end == null ? "—" : Number(r.gini_end).toFixed(2)}` }),
      el("a", { href: `/api/runs/${r.run_id}/report`, target: "_blank", text: "Report" }),
      el("a", { href: `/api/runs/${r.run_id}/bundle`, text: "Data" }))));
  } catch (_) { /* ignore */ }
}

/* ================================================================ views */
function showSetup() {
  S.viewingSetup = true;
  $("#setup").hidden = false;
  $("#live").hidden = true;
  $("#btnNew").hidden = true;
  loadPastRuns();
}
function showLive() {
  $("#setup").hidden = true;
  $("#live").hidden = false;
  net.resize();
}
function isActive(st) { return ["created", "starting", "running", "paused", "finishing"].includes(st); }

function setStatus(st) {
  S.status = st;
  const active = isActive(st);
  $("#btnPause").hidden = st !== "running";
  $("#btnResume").hidden = st !== "paused";
  $("#btnStop").hidden = !active;
  const done = ["completed", "stopped", "failed"].includes(st);
  $("#btnReport").hidden = !(done && S.runId && st !== "failed") ;
  $("#btnBundle").hidden = !(done && S.runId && st !== "failed");
  $("#btnNew").hidden = !done || S.viewingSetup;
  if (S.runId) {
    $("#btnReport").href = `/api/runs/${S.runId}/report`;
    $("#btnBundle").href = `/api/runs/${S.runId}/bundle`;
  }
  const label = { running: "Running", paused: "Paused", starting: "Starting", finishing: "Writing report", completed: "Completed", stopped: "Stopped", failed: "Failed", idle: "Idle", created: "Starting" }[st] || st;
  $("#status").textContent = S.steps ? `${label} · step ${S.step}/${S.steps}` : label;
  $("#progressFill").style.width = S.steps ? `${(100 * S.step) / S.steps}%` : "0";
}

/* ================================================================ roster */
function upsertAgents(list) {
  for (const a of list) S.agents.set(a.id, { ...(S.agents.get(a.id) || {}), ...a });
  renderRoster();
}
function renderRoster() {
  const agents = [...S.agents.values()].sort((a, b) => (b.worth ?? 0) - (a.worth ?? 0));
  const max = Math.max(1, ...agents.map((a) => a.worth || 0));
  $("#roster").replaceChildren(...agents.map((a) => {
    const d = a.start_worth ? (a.worth / a.start_worth - 1) * 100 : 0;
    const li = el("li", { class: "agent", tabindex: "0", role: "button", "aria-label": `${a.id}, ${a.label}`,
      onclick: () => openAgent(a.id), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openAgent(a.id); } } },
      el("i", { class: "dot", style: { background: a.color } }),
      el("span", {}, el("span", { class: "name", text: a.id }), " ", el("span", { class: "mood", text: a.mood || "" })),
      el("span", { class: "worth" }, fmt(a.worth, 0), " ", el("span", { class: `delta ${d >= 0 ? "up" : "down"}`, text: `${d >= 0 ? "+" : ""}${d.toFixed(0)}%` })),
      el("span", { class: "arch", text: a.label + (a.syndicate ? ` · ${a.syndicate}` : "") }),
      el("span", { class: "meter" }, el("i", { style: { width: `${(100 * Math.max(0, a.worth || 0)) / max}%`, background: a.color } })),
      (a.reneges ? el("span", { class: "flag", text: `${a.reneges} broken promise${a.reneges > 1 ? "s" : ""}` }) : null));
    return li;
  }));
}

function openAgent(id) {
  const a = S.agents.get(id);
  if (!a) return;
  const traits = a.traits || {};
  const rel = Object.entries(a.relations || {}).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1])).slice(0, 12);
  const inv = Object.entries(a.inventory || {});
  $("#agentBody").replaceChildren(
    el("h2", {}, el("i", { class: "dot", style: { background: a.color, width: "14px", height: "14px", marginRight: "10px" } }), a.id),
    el("p", { class: "sub", text: `${a.label}${a.model ? " · " + a.model : ""} · worth ${fmt(a.worth)} (cash ${fmt(a.cash)})` }),
    a.thought ? el("p", { class: "quote", text: a.thought }) : null,
    el("h3", { text: "Private objective" }), el("p", { text: a.objective || "—" }),
    el("h3", { text: "Current plan" }), el("p", { text: a.strategy || "—" }),
    el("h3", { text: "Backstory" }), el("p", { text: a.backstory || "—" }),
    el("h3", { text: "Traits" }),
    el("div", { class: "traits" }, ...Object.entries(traits).flatMap(([k, v]) => [
      el("span", { text: k.replace("_", " ") }), el("span", { class: "bar" }, el("i", { style: { width: `${v * 100}%` } })), el("span", { text: v.toFixed(2) })])),
    el("h3", { text: "How they feel about others" }),
    rel.length ? el("div", { class: "rel" }, ...rel.map(([k, v]) => el("span", { style: { borderColor: v < -0.2 ? "var(--neg)" : v > 0.3 ? "var(--pos)" : "var(--rule)" }, text: `${k} ${v > 0 ? "+" : ""}${v.toFixed(2)}` })))
      : el("p", { class: "hint", text: "No relationships formed yet." }),
    el("h3", { text: "Holdings" }),
    el("p", { text: inv.length ? inv.map(([k, v]) => `${v} ${k}`).join(", ") : "nothing" }),
    (a.actions && a.actions.length) ? el("div", {}, el("h3", { text: "Last actions" }), el("div", { class: "rel" }, ...a.actions.map((x) => el("span", { text: x })))) : null,
  );
  $("#agentDlg").showModal();
}

/* ================================================================ feed */
function buildFilters() {
  $("#filters").replaceChildren(...Object.entries(FILTER_LABELS).map(([k, label]) => el("button", {
    class: "chip", "aria-pressed": String(S.filters[k]), text: label,
    onclick: (e) => { S.filters[k] = !S.filters[k]; e.target.setAttribute("aria-pressed", String(S.filters[k])); applyFilters(); },
  })));
}
function applyFilters() { $$("#feed > li").forEach((li) => { li.hidden = !S.filters[li.dataset.cat]; }); }

function who(id) {
  const a = S.agents.get(id);
  return el("span", { class: "who" }, el("i", { class: "dot", style: { background: a ? a.color : "var(--ink-3)" } }), id || "World");
}

function feedItem(ev) {
  if (HIDDEN_KINDS.has(ev.kind)) return null;
  const cat = CAT[ev.kind] || "system";
  let cls = `ev ${ev.kind}`;
  if (cat === "deals") cls += " deal";
  if (cat === "conflict") cls += " conflict";
  if (ev.kind === "highlight" || ev.kind === "stock_collapse" || ev.kind === "rule_enacted") cls += " alert";
  if (cat === "system") cls += " system";
  const head = el("div", {}, el("span", { class: "st", text: `step ${ev.step}` }));
  const body = el("div", { class: "txt" });
  switch (ev.kind) {
    case "thought": {
      head.append(who(ev.actor), el("span", { class: "tag", text: ev.data.mood ? ` · ${ev.data.mood}` : "" }));
      body.textContent = ev.text || "(no thought)";
      const li = el("li", { class: cls, "data-cat": cat }, head, body);
      if (ev.data.actions && ev.data.actions.length) li.append(el("div", { class: "acts" }, ...ev.data.actions.map((a) => el("span", { text: a }))));
      return li;
    }
    case "dm":
      head.append(who(ev.actor), el("span", { class: "tag", text: ` → ${ev.targets.join(", ")} (private)` }));
      body.textContent = ev.text; break;
    case "syndicate_msg":
      head.append(who(ev.actor), el("span", { class: "tag", text: ` → “${ev.data.name}” (syndicate)` }));
      body.textContent = ev.text; break;
    case "say":
      head.append(who(ev.actor)); body.textContent = ev.text; break;
    case "highlight":
      body.append(el("b", { text: ev.data.title }), ev.data.body); break;
    case "summary":
      head.append(el("span", { class: "who", text: `Chronicle, steps ${ev.data.start}–${ev.data.end}` }));
      body.textContent = ev.text; break;
    default:
      if (ev.actor) head.append(who(ev.actor));
      body.textContent = ev.text || ev.kind;
  }
  return el("li", { class: cls, "data-cat": cat }, head, body);
}

function pushFeed(ev, { prepend = true } = {}) {
  const li = feedItem(ev);
  if (!li) return;
  li.hidden = !S.filters[li.dataset.cat];
  const feed = $("#feed");
  if (prepend) feed.prepend(li); else feed.append(li);
  while (feed.childElementCount > 600) feed.lastElementChild.remove();
}

function pushHighlight(h) {
  S.highlights.push(h);
  $("#hlCount").textContent = S.highlights.length;
  const li = el("li", { class: "ev alert" }, el("div", {}, el("span", { class: "st", text: `step ${h.step}` }), el("span", { class: "who", text: h.category.replace("_", " ") })),
    el("div", { class: "txt" }, el("b", { text: h.title }), h.body));
  $("#highlights").prepend(li);
}
function pushChronicle(c) {
  const box = $("#chronicle");
  if (box.querySelector(".empty")) box.replaceChildren();
  box.append(el("p", {}, el("b", { text: `Steps ${c.start}–${c.end}` }), c.text));
}

/* ================================================================ world strip */
function renderWorld(w) {
  if (!w || !w.metrics) return;
  const m = w.metrics;
  const box = $("#world");
  const parts = [];
  const kv = (k, v) => el("span", {}, `${k} `, el("b", { text: v }));
  if (S.envKey === "marketplace") {
    for (const [g, p] of Object.entries(m.prices || {})) parts.push(kv(g, fmt(p)));
    parts.push(kv("open orders", m.open_orders ?? 0));
    if (m.hungry) parts.push(kv("hungry", m.hungry));
  } else if (S.envKey === "auction_house") {
    parts.push(kv("lots sold", m.lots_sold), kv("house revenue", fmt(m.house_revenue, 0)), kv("winner's curse rate", `${Math.round((m.curse_rate || 0) * 100)}%`));
    for (const l of (w.lots || []).slice(0, 4)) parts.push(el("span", {}, `${l.id} ${l.item} (${l.format.replace("_", "-")}) `, el("b", { text: l.format === "english" ? (l.high_bidder ? `${fmt(l.high_bid, 0)} by ${l.high_bidder}` : "no bids") : `${l.bids} sealed bid${l.bids === 1 ? "" : "s"}` })));
  } else if (S.envKey === "commons") {
    parts.push(el("span", {}, "stock ", el("b", { text: `${Math.round(m.stock_fraction * 100)}%` }), el("span", { class: "gauge" }, el("i", { style: { width: `${m.stock_fraction * 100}%`, background: m.stock_fraction < 0.25 ? "var(--neg)" : "var(--ink)" } }))));
    parts.push(kv("catch", fmt(m.harvest, 0)), kv("price", fmt(m.price)), kv("quota", m.quota == null ? "none" : fmt(m.quota, 1)),
      kv("tax", fmt(m.tax)), kv("monitoring", `${Math.round((m.monitoring || 0) * 100)}%`));
    for (const p of w.proposals || []) parts.push(kv(`vote ${p.id} (${p.rule} ${p.value ?? "none"})`, `${p.yes}–${p.no}`));
    if (m.collapsed) parts.push(el("b", { style: { color: "var(--neg)" }, text: "COLLAPSED" }));
  }
  if (w.syndicates && w.syndicates.length) parts.push(kv("syndicates", w.syndicates.map((s) => `${s.name} (${s.members.length})`).join(", ")));
  box.replaceChildren(...parts);
}

/* ================================================================ charts */
const charts = {};
function chartDefaults() {
  Chart.defaults.font.family = css("--sans") || "system-ui";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--rule");
  Chart.defaults.animation = false;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 2;
  Chart.defaults.plugins.tooltip.mode = "index";
  Chart.defaults.plugins.tooltip.intersect = false;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
  Chart.defaults.elements.line.borderWidth = 2;
}
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];

function makeCharts() {
  for (const c of Object.values(charts)) c.destroy();
  const grid = { color: css("--rule") };
  const base = (extra = {}) => ({ responsive: true, scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } }, y: { grid, beginAtZero: true, ticks: { maxTicksLimit: 5 } } },
    plugins: { legend: { position: "bottom" }, tooltip: {} }, ...extra });
  charts.ineq = new Chart($("#chIneq"), { type: "line", data: { labels: [], datasets: [
    { label: "Gini", data: [], borderColor: SERIES[0], backgroundColor: SERIES[0] },
    { label: "Top-20% share", data: [], borderColor: SERIES[1], backgroundColor: SERIES[1] }] },
    options: (() => { const o = base(); o.scales.y.max = 1; return o; })() });
  charts.vol = new Chart($("#chVol"), { type: "bar", data: { labels: [], datasets: [{ label: "coins", data: [], backgroundColor: SERIES[0], borderRadius: 3, maxBarThickness: 18 }] },
    options: (() => { const o = base(); o.plugins.legend.display = false; return o; })() });
  if (S.envKey === "marketplace") {
    $("#chEnvTitle").textContent = "Reference prices";
    charts.env = new Chart($("#chEnv"), { type: "line", data: { labels: [], datasets: [] }, options: (() => { const o = base(); o.scales.y.beginAtZero = false; o.scales.y.type = "logarithmic"; return o; })() });
  } else if (S.envKey === "commons") {
    $("#chEnvTitle").textContent = "Fish stock (share of capacity)";
    charts.env = new Chart($("#chEnv"), { type: "line", data: { labels: [], datasets: [
      { label: "stock S/K", data: [], borderColor: SERIES[2], backgroundColor: SERIES[2] },
      { label: "MSY level K/2", data: [], borderColor: css("--ink-3"), borderDash: [4, 4], borderWidth: 1 }] },
      options: (() => { const o = base(); o.scales.y.max = 1; return o; })() });
  } else {
    $("#chEnvTitle").textContent = "Price paid vs true value";
    charts.env = new Chart($("#chEnv"), { type: "scatter", data: { datasets: ["first_price", "vickrey", "english"].map((f, i) => ({
      label: f.replace("_", "-"), data: [], backgroundColor: SERIES[i], borderColor: css("--surface"), borderWidth: 1.5, pointRadius: 4.5, pointHoverRadius: 6 })).concat([
      { type: "line", label: "price = value", data: [], borderColor: css("--ink-3"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0 }]) },
      options: (() => { const o = base(); o.plugins.tooltip.mode = "nearest"; o.plugins.tooltip.intersect = true;
        o.plugins.legend.labels = { usePointStyle: true, pointStyle: "circle", boxWidth: 7, boxHeight: 7 };
        o.plugins.tooltip.callbacks = { label: (c) => c.raw.label || `${fmt(c.raw.x, 0)} → ${fmt(c.raw.y, 0)}` };
        o.scales.x = { type: "linear", grid, title: { display: true, text: "true value" }, ticks: { maxTicksLimit: 5 } };
        o.scales.y.title = { display: true, text: "price" }; return o; })() });
  }
}

function updateCharts() {
  const rows = S.metrics;
  const labels = rows.map((r) => r.step);
  if (!charts.ineq) return;
  charts.ineq.data.labels = labels;
  charts.ineq.data.datasets[0].data = rows.map((r) => r.gini);
  charts.ineq.data.datasets[1].data = rows.map((r) => r.top20_share);
  charts.ineq.update("none");
  charts.vol.data.labels = labels;
  charts.vol.data.datasets[0].data = rows.map((r) => r.trade_value);
  charts.vol.update("none");
  const env = charts.env;
  if (!env) return;
  if (S.envKey === "marketplace" && rows.length) {
    const goods = Object.keys(rows[rows.length - 1].env.prices || {});
    if (env.data.datasets.length !== goods.length) {
      env.data.datasets = goods.map((g, i) => ({ label: g, data: [], borderColor: SERIES[i % SERIES.length], backgroundColor: SERIES[i % SERIES.length] }));
    }
    env.data.labels = labels;
    goods.forEach((g, i) => { env.data.datasets[i].data = rows.map((r) => (r.env.prices || {})[g]); });
    env.update("none");
  } else if (S.envKey === "commons") {
    env.data.labels = labels;
    env.data.datasets[0].data = rows.map((r) => r.env.stock_fraction);
    env.data.datasets[1].data = rows.map(() => 0.5);
    env.update("none");
  } else if (S.envKey === "auction_house") {
    const by = { first_price: [], vickrey: [], english: [] };
    for (const l of S.lots) (by[l.format] || []).push({ x: l.true_value, y: l.price, label: `${l.item}: ${l.winner} paid ${fmt(l.price, 0)} (value ${fmt(l.true_value, 0)})` });
    env.data.datasets[0].data = by.first_price; env.data.datasets[1].data = by.vickrey; env.data.datasets[2].data = by.english;
    const mx = Math.max(10, ...S.lots.map((l) => Math.max(l.true_value, l.price)));
    env.data.datasets[3].data = [{ x: 0, y: 0 }, { x: mx, y: mx }];
    env.update("none");
  }
  const last = rows[rows.length - 1];
  if (last) $("#giniBadge").textContent = `Gini ${last.gini.toFixed(2)}`;
}

/* ================================================================ network */
const net = (() => {
  const canvas = $("#net");
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  const nodes = new Map();
  let links = [];
  const pulses = [];
  const glows = new Map();
  let hover = null, drag = null;

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    W = r.width; H = r.height;
    canvas.width = Math.max(1, W * dpr); canvas.height = Math.max(1, H * dpr);
  }
  new ResizeObserver(resize).observe(canvas);

  function setNodes(list) {
    const ids = new Set(list.map((n) => n.id));
    for (const id of [...nodes.keys()]) if (!ids.has(id)) nodes.delete(id);
    list.forEach((n, i) => {
      const old = nodes.get(n.id);
      if (old) Object.assign(old, n);
      else {
        // Canvas may still be hidden (0×0) when agents arrive: seed on a ring of a nominal size plus jitter.
        const w = W || 800, hgt = H || 500;
        const ang = (i / list.length) * Math.PI * 2;
        const rad = Math.min(w, hgt) * 0.33;
        nodes.set(n.id, { ...n, x: w / 2 + Math.cos(ang) * rad + (Math.random() - 0.5) * 8,
          y: hgt / 2 + Math.sin(ang) * rad + (Math.random() - 0.5) * 8, vx: 0, vy: 0 });
      }
    });
  }
  function update(snap) {
    if (!snap) return;
    setNodes(snap.nodes.map((n) => {
      const a = S.agents.get(n.id) || {};
      return { id: n.id, color: n.color || a.color, worth: n.worth ?? a.worth ?? 0, syndicate: n.syndicate };
    }));
    links = snap.links;
  }
  function pulse(a, b, kind) {
    if (!nodes.has(a) || !nodes.has(b)) return;
    pulses.push({ a, b, t0: performance.now(), neg: kind === "neg" });
    if (pulses.length > 80) pulses.shift();
  }
  function glow(id) { glows.set(id, performance.now()); }
  function radius(n, max) {
    const scale = Math.max(0.55, Math.min(1, Math.min(W, H) / 520));  // smaller nodes on small canvases
    return (7 + 15 * Math.sqrt(Math.max(0, n.worth) / max)) * scale;
  }

  function tick() {
    if (!W || !H) return;
    const arr = [...nodes.values()];
    const k = Math.min(W, H) || 400;
    for (let i = 0; i < arr.length; i++) for (let j = i + 1; j < arr.length; j++) {
      const a = arr[i], b = arr[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      if (dx === 0 && dy === 0) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; }  // break exact overlaps
      const d2 = Math.max(dx * dx + dy * dy, 25);
      const f = (k * k * 0.012) / d2; const d = Math.sqrt(d2); dx /= d; dy /= d;
      a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
    const maxW = Math.max(1, ...links.map((l) => l.weight));
    for (const l of links) {
      const a = nodes.get(l.source), b = nodes.get(l.target); if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y; const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = k * (l.valence < 0 ? 0.42 : 0.24);
      const s = 0.004 + 0.02 * (l.weight / maxW);
      const f = (d - rest) * s; a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const n of arr) {
      n.vx += (W / 2 - n.x) * 0.003; n.vy += (H / 2 - n.y) * 0.003;
      if (n === drag) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82; n.x += n.vx; n.y += n.vy;
      n.x = Math.max(24, Math.min(W - 24, n.x)); n.y = Math.max(24, Math.min(H - 40, n.y));
    }
  }

  function draw(now) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const ink = css("--ink"), ink3 = css("--ink-3"), edge = css("--edge"), neg = css("--neg"), brass = css("--brass"), surface = css("--surface");
    const maxW = Math.max(1, ...links.map((l) => l.weight));
    for (const l of links) {
      const a = nodes.get(l.source), b = nodes.get(l.target); if (!a || !b) continue;
      const focus = hover && (hover.id === l.source || hover.id === l.target);
      ctx.strokeStyle = l.valence < 0 ? neg : edge;
      ctx.globalAlpha = hover ? (focus ? 0.95 : 0.12) : 0.55;
      ctx.lineWidth = 0.6 + 4 * (l.weight / maxW);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i]; const t = (now - p.t0) / 1400;
      if (t > 1) { pulses.splice(i, 1); continue; }
      const a = nodes.get(p.a), b = nodes.get(p.b); if (!a || !b) continue;
      ctx.strokeStyle = p.neg ? neg : brass; ctx.globalAlpha = 1 - t; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      const x = a.x + (b.x - a.x) * Math.min(1, t * 1.6), y = a.y + (b.y - a.y) * Math.min(1, t * 1.6);
      ctx.fillStyle = p.neg ? neg : brass; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;
    const maxWorth = Math.max(1, ...[...nodes.values()].map((n) => n.worth || 0));
    ctx.font = `500 12px ${css("--sans") || "system-ui"}`; ctx.textAlign = "center";
    for (const n of nodes.values()) {
      const r = radius(n, maxWorth);
      const g = glows.get(n.id);
      if (g && now - g < 1600) {
        const t = (now - g) / 1600; ctx.strokeStyle = n.color; ctx.globalAlpha = 0.5 * (1 - t); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 4 + 10 * t, 0, Math.PI * 2); ctx.stroke(); ctx.globalAlpha = 1;
      }
      if (n.syndicate) { ctx.setLineDash([3, 3]); ctx.strokeStyle = ink3; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]); }
      ctx.fillStyle = n.color; ctx.strokeStyle = surface; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = hover && hover !== n ? ink3 : ink;
      ctx.fillText(n.id, n.x, n.y + r + 13);
    }
  }
  function loop(now) { if (!document.hidden) { tick(); draw(now); } requestAnimationFrame(loop); }
  requestAnimationFrame(loop);

  function pick(ev) {
    const r = canvas.getBoundingClientRect(); const x = ev.clientX - r.left, y = ev.clientY - r.top;
    const maxWorth = Math.max(1, ...[...nodes.values()].map((n) => n.worth || 0));
    for (const n of nodes.values()) { const rr = radius(n, maxWorth) + 6; if ((n.x - x) ** 2 + (n.y - y) ** 2 < rr * rr) return { n, x, y }; }
    return { n: null, x, y };
  }
  const tip = $("#netTip");
  canvas.addEventListener("pointermove", (ev) => {
    const { n, x, y } = pick(ev);
    if (drag) { drag.x = x; drag.y = y; return; }
    hover = n;
    if (n) {
      const a = S.agents.get(n.id) || {};
      tip.replaceChildren(el("b", { text: n.id }), ` · ${a.label || ""}`, el("br"), `worth ${fmt(n.worth, 0)}`,
        a.thought ? el("div", { style: { marginTop: "4px", fontFamily: "var(--serif)", fontStyle: "italic" }, text: a.thought.slice(0, 160) + (a.thought.length > 160 ? "…" : "") }) : null);
      tip.hidden = false; tip.style.left = `${Math.min(x + 14, W - 290)}px`; tip.style.top = `${y + 14}px`;
      canvas.style.cursor = "pointer";
    } else { tip.hidden = true; canvas.style.cursor = "grab"; }
  });
  canvas.addEventListener("pointerleave", () => { hover = null; tip.hidden = true; });
  canvas.addEventListener("pointerdown", (ev) => { const { n } = pick(ev); if (n) { drag = n; drag.moved = false; canvas.setPointerCapture(ev.pointerId); } });
  canvas.addEventListener("pointerup", (ev) => {
    if (drag) { const n = drag; drag = null; const { n: hit } = pick(ev); if (hit === n) openAgent(n.id); }
  });
  return { update, pulse, glow, resize, clear: () => { nodes.clear(); links = []; }, inspect: () => ({ W, H, nodes: [...nodes.values()].map((n) => [n.id, n.x, n.y, n.worth]) }) };
})();

function renderLegend() {
  const present = new Map();
  for (const a of S.agents.values()) present.set(a.archetype, a);
  $("#netLegend").replaceChildren(...[...present.values()].map((a) => el("span", {}, el("i", { class: "dot", style: { background: a.color } }), a.label)),
    el("span", {}, el("i", { class: "dot", style: { background: "var(--neg)", borderRadius: "1px", height: "3px" } }), "hostile tie"));
}

/* ================================================================ state & events */
function resetLive() {
  S.agents.clear(); S.metrics = []; S.highlights = []; S.lots = []; S.step = 0;
  $("#feed").replaceChildren(); $("#highlights").replaceChildren();
  $("#chronicle").replaceChildren(el("p", { class: "empty", text: "The chronicler writes a dispatch every few steps." }));
  $("#hlCount").textContent = "0";
  net.clear();
}

function applySnapshot(s) {
  if (!s || s.status === "idle") { setStatus("idle"); showSetup(); return; }
  resetLive();
  S.runId = s.run_id; S.cfg = s.config; S.envKey = s.environment; S.steps = s.steps; S.step = s.step;
  $("#runinfo").textContent = `${s.name} — ${s.env_title}, ${s.agents.length} agents, minds: ${s.config.llm.backend === "ollama" ? s.config.llm.model : "heuristic baseline"}`;
  upsertAgents(s.agents);
  renderLegend();
  try { makeCharts(); } catch (e) { console.error("chart setup failed", e); }
  S.metrics = s.metrics || [];
  for (const ev of s.feed || []) if (ev.kind === "lot_closed") S.lots.push(ev.data);
  updateCharts();
  (s.highlights || []).forEach(pushHighlight);
  (s.chronicle || []).forEach(pushChronicle);
  (s.feed || []).forEach((ev) => pushFeed(ev));
  net.update(s.network);
  renderWorld(s.world);
  renderTelemetry(s.llm);
  setStatus(s.status);
  if (isActive(s.status) || !S.viewingSetup) showLive(); else showSetup();
}

function renderTelemetry(l) {
  if (!l) return;
  if (l.backend === "heuristic") { $("#telemetry").textContent = "Minds: heuristic baseline (no LLM calls)"; return; }
  $("#telemetry").textContent = `LLM calls ${l.calls} · avg ${l.avg_latency_s}s · tokens ${((l.prompt_tokens + l.completion_tokens) / 1000).toFixed(1)}k · in flight ${l.in_flight} · failures ${l.failures}` + (l.last_error ? ` · last error: ${l.last_error.slice(0, 80)}` : "");
}

function onEvent(ev) {
  if (ev.step != null && ev.step > S.step && ev.kind !== "sim_started") S.step = ev.step;
  switch (ev.kind) {
    case "sim_started":
      resetLive();
      S.cfg = ev.data.config; S.runId = ev.data.config.run_id; S.envKey = ev.data.environment.key; S.steps = ev.data.config.steps;
      $("#runinfo").textContent = `${S.cfg.name} — ${ev.data.environment.title}, ${S.cfg.num_agents} agents, minds: ${S.cfg.llm.backend === "ollama" ? S.cfg.llm.model : "heuristic baseline"}`;
      upsertAgents(Object.values(ev.data.personas).map((p) => ({ id: p.id, archetype: p.archetype, label: p.archetype_label, color: p.color, model: p.model,
        objective: p.objective, backstory: p.backstory, traits: p.traits, worth: 0, start_worth: 0 })));
      renderLegend();
      try { makeCharts(); } catch (e) { console.error("chart setup failed", e); }
      net.update({ nodes: [...S.agents.values()].map((a) => ({ id: a.id, color: a.color, worth: 1 })), links: [] });
      setStatus("running"); S.viewingSetup = false; showLive();
      break;
    case "thought": {
      const a = S.agents.get(ev.actor);
      if (a) { a.thought = ev.text; a.mood = ev.data.mood; a.actions = ev.data.actions; a.strategy = ev.data.strategy || a.strategy; }
      net.glow(ev.actor);
      break;
    }
    case "metrics": {
      S.metrics.push(ev.data);
      for (const [id, w] of Object.entries(ev.data.worths || {})) {
        const a = S.agents.get(id); if (a) { if (!a.start_worth) a.start_worth = w; a.worth = w; }
      }
      updateCharts(); renderRoster();
      break;
    }
    case "step_completed":
      net.update(ev.data.network); renderWorld(ev.data.world); renderTelemetry(ev.data.llm);
      refreshAgentsSoon();
      setStatus(S.status === "idle" ? "running" : S.status);
      break;
    case "lot_closed": S.lots.push(ev.data); break;
    case "highlight": pushHighlight(ev.data); break;
    case "summary": pushChronicle(ev.data); break;
    case "sim_paused": setStatus("paused"); break;
    case "sim_resumed": setStatus("running"); break;
    case "sim_completed": case "sim_failed":
      S.step = ev.step;
      setStatus(ev.data.status || (ev.kind === "sim_failed" ? "failed" : "completed"));
      if (ev.kind === "sim_failed" && ev.text) pushFeed({ ...ev, kind: "highlight", data: { title: "Simulation failed", body: ev.text } });
      loadPastRuns();
      break;
  }
  if (["trade", "contract_settled", "contract_accepted", "dm", "syndicate_msg", "lot_closed"].includes(ev.kind)) for (const t of ev.targets || []) net.pulse(ev.actor, t, "pos");
  if (["contract_reneged", "contract_defaulted", "accusation"].includes(ev.kind)) for (const t of ev.targets || []) net.pulse(ev.actor, t, "neg");
  if (ev.kind !== "sim_failed") pushFeed(ev);
  if (ev.kind !== "metrics" && ev.kind !== "step_completed") setStatus(S.status);
}

let refreshTimer = null;
function refreshAgentsSoon() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    try { const s = await api("/api/sim/state"); if (s.agents) upsertAgents(s.agents); } catch (_) { /* ignore */ }
  }, 400);
}

/* ================================================================ websocket */
let ws, backoff = 500;
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { backoff = 500; };
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.type === "snapshot") applySnapshot(msg.state);
    else if (msg.type === "reset") { S.viewingSetup = false; }
    else if (msg.type === "event") onEvent(msg.event);
  };
  ws.onclose = () => { setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 8000); };
}
setInterval(() => { if (ws && ws.readyState === 1) ws.send("ping"); }, 20000);

/* ================================================================ boot */
async function boot() {
  chartDefaults();
  buildFilters();
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => {
    $$(".tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $$("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
    $("#filters").hidden = b.dataset.tab !== "feed";
  }));
  $("#btnPause").onclick = () => api("/api/sim/pause", { method: "POST" }).then(() => setStatus("paused")).catch(() => {});
  $("#btnResume").onclick = () => api("/api/sim/resume", { method: "POST" }).then(() => setStatus("running")).catch(() => {});
  $("#btnStop").onclick = () => { if (confirm("Stop the simulation and write the report?")) api("/api/sim/stop", { method: "POST" }).catch(() => {}); };
  $("#btnNew").onclick = () => showSetup();
  try {
    S.meta = await api("/api/meta");
    buildSetup();
    if (S.meta.defaults) {
      const d = S.meta.defaults;
      $("input[name=base_url]").value = d.llm.base_url;
      $("#modelSelect").replaceChildren(el("option", { value: d.llm.model, text: d.llm.model }));
    }
    checkOllama();
  } catch (e) {
    $("#formError").textContent = `Could not load configuration: ${e.message}`; $("#formError").hidden = false;
  }
  loadPastRuns();
  connect();
}
window.__agora = { net, S };
boot();
