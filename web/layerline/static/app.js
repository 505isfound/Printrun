// Layerline interface. Phase 0: connection + console. Phase 1: temperatures & safety.
"use strict";

const $ = (id) => document.getElementById(id);
const COMMON_BAUDS = [115200, 250000];
const MAX_LOG_LINES = 3000;
const HISTORY_SECONDS = 15 * 60;

const ui = {
  state: null,
  ports: [],
  bauds: [],
  baud: 115200,
  history: [],
  historyPos: -1,
  draft: "",
  ws: null,
  temps: [],            // temperature samples for the graph
  settingsInfo: {},
  editing: new Set(),   // target inputs the user is typing in (don't overwrite)
};

// Friendlier wording for settings than the classic labels
const SETTINGS_LAYOUT = [
  { title: "Display", items: [
    ["statuspanel", "Status strip", "The bar at the top with temperatures and print progress"],
    ["tempgraph", "Temperature graph", ""],
    ["tempgauges", "Temperature bars", "A bar under each temperature showing how close it is to its target"],
    ["lockbox", "Show the lock button", "Lock disables the controls so nothing gets bumped mid-print"],
    ["lockonstart", "Lock when a print starts", "Only when the lock button is shown"],
  ]},
  { title: "Printer", items: [
    ["monitor", "Check temperatures regularly", "Needed for the readouts and graph"],
    ["extruders", "Number of nozzles", ""],
    ["heaters_off_on_exit", "Turn heaters off when closing", "Off by default: closing leaves heaters as they are"],
    ["dtr", "Reset board when connecting", "Turn off if your printer restarts every time you connect"],
    ["display_progress_on_printer", "Show progress on the printer's screen", "Not every printer supports this"],
    ["printer_progress_update_interval", "Update the printer's screen every", "", "s"],
  ]},
  { title: "Preheat temperatures", items: [
    ["temperature_pla", "PLA nozzle", ""], ["bedtemp_pla", "PLA bed", ""],
    ["temperature_petg", "PETG nozzle", ""], ["bedtemp_petg", "PETG bed", ""],
    ["temperature_abs", "ABS nozzle", ""], ["bedtemp_abs", "ABS bed", ""],
  ]},
];

// Drop the access key from the address bar (the app already stored it as a cookie)
if (location.search.includes("key=")) history.replaceState(null, "", "/");

// ---------- connection to the app ----------
function send(msg) {
  if (ui.ws && ui.ws.readyState === WebSocket.OPEN) ui.ws.send(JSON.stringify(msg));
}

function openSocket() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ui.ws = ws;
  ws.onopen = () => { $("banner").hidden = true; };
  ws.onclose = () => { $("banner").hidden = false; setTimeout(openSocket, 1500); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") renderState(msg.state);
    else if (msg.type === "log") addLog(msg);
    else if (msg.type === "temps") addSample(msg.sample);
    else if (msg.type === "ports") { ui.ports = msg.ports; renderPorts(); }
    else if (msg.type === "confirm_close") confirmClose();
    else if (msg.type === "open_url") window.open(msg.url, msg.name, "width=1040,height=680");
  };
}

async function start() {
  const res = await fetch("/api/initial");
  const data = await res.json();
  ui.ports = data.ports;
  ui.bauds = data.bauds;
  ui.history = data.history || [];
  ui.temps = data.temp_history || [];
  ui.settingsInfo = data.settings_info || {};
  ui.nativeDialogs = !!data.native_dialogs;
  ui.baud = data.state.settings.baudrate || 115200;
  renderPorts(data.state.settings.port);
  renderBauds();
  data.log.forEach(addLog);
  renderState(data.state);
  drawGraphs();
  openSocket();
}

// ---------- confirm sheet ----------
function ask(title, body, okLabel, danger = true, cancelLabel = "Not now") {
  return new Promise((resolve) => {
    $("confirm-cancel").textContent = cancelLabel;
    $("confirm-title").textContent = title;
    $("confirm-body").textContent = body;
    const ok = $("confirm-ok");
    ok.textContent = okLabel;
    ok.className = "btn " + (danger ? "danger" : "primary");
    const sheet = $("confirm-sheet");
    sheet.hidden = false;
    $("confirm-cancel").focus();
    const done = (v) => { sheet.hidden = true; ok.onclick = null; $("confirm-cancel").onclick = null; resolve(v); };
    ok.onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

function printing() {
  const s = ui.state;
  return !!(s && (s.printing || s.sdprinting) && !s.paused);
}

async function confirmClose() {
  if (await ask("A print is running", "Closing stops sending the print to the printer. Heaters are left as they are.", "Close anyway")) {
    send({ action: "quit" });
  }
}

// ---------- connection card ----------
function renderPorts(preferred) {
  const sel = $("port");
  const keep = preferred ?? sel.value;
  sel.innerHTML = "";
  const add = (value, label) => sel.add(new Option(label, value));
  add("auto", "Find automatically");
  ui.ports.forEach((p) => add(p, p));
  add("other", "Other…");
  if (keep && keep !== "auto" && keep !== "other" && !ui.ports.includes(keep)) {
    if (keep.includes(":")) { sel.value = "other"; $("custom-port").value = keep; }
    else sel.value = "auto";
  } else {
    sel.value = keep || "auto";
  }
  $("custom-row").hidden = sel.value !== "other";
}

function renderBauds() {
  const seg = $("baud");
  seg.innerHTML = "";
  const isOther = !COMMON_BAUDS.includes(ui.baud);
  [...COMMON_BAUDS.map(String), "Other"].forEach((label) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label === "Other" ? "Other" : Number(label).toLocaleString("en-US");
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(label === "Other" ? isOther : Number(label) === ui.baud));
    b.onclick = () => {
      if (label === "Other") { if (COMMON_BAUDS.includes(ui.baud)) ui.baud = 57600; }
      else ui.baud = Number(label);
      renderBauds();
    };
    seg.appendChild(b);
  });
  const other = $("baud-other");
  other.hidden = !isOther;
  other.innerHTML = "";
  ui.bauds.forEach((b) => other.add(new Option(b.toLocaleString("en-US"), b)));
  other.value = String(ui.baud);
}
$("baud-other").onchange = (e) => { ui.baud = Number(e.target.value); };
$("port").onchange = (e) => {
  $("custom-row").hidden = e.target.value !== "other";
  if (e.target.value === "other") $("custom-port").focus();
};
$("rescan").onclick = (e) => { e.preventDefault(); send({ action: "rescan" }); };
$("reset").onclick = async () => {
  if (printing() && !(await ask("A print is running", "Resetting the printer's board stops the print.", "Reset anyway"))) return;
  send({ action: "reset" });
};
$("connect").onclick = async () => {
  const s = ui.state;
  if (s && (s.online || s.connecting)) {
    if (printing() && !(await ask("A print is running", "Disconnecting stops sending the print to the printer. Heaters are left as they are.", "Disconnect"))) return;
    send({ action: "disconnect" });
    return;
  }
  let port = $("port").value;
  if (port === "other") {
    port = $("custom-port").value.trim();
    if (!port) { $("custom-port").focus(); return; }
  }
  send({ action: "connect", port, baud: ui.baud });
};

// ---------- temperatures ----------
function tempText(now, target, online) {
  if (!online) return { now: "—", sub: "Not connected", status: "" };
  const n = Math.round(now);
  const t = Math.round(target);
  if (!t) return { now: `${n}°`, sub: n > 45 ? "Off, cooling down" : "Off", status: "off" };
  if (n < t - 3) return { now: `${n}°`, sub: `Heating to ${t}°`, status: "heating" };
  if (n > t + 3) return { now: `${n}°`, sub: `Cooling to ${t}°`, status: "cooling" };
  return { now: `${n}°`, sub: `Holding ${t}°`, status: "holding" };
}

document.querySelectorAll("form.heater").forEach((form) => {
  const heater = form.dataset.heater;
  const input = form.querySelector("input");
  input.addEventListener("focus", () => ui.editing.add(heater));
  input.addEventListener("blur", () => ui.editing.delete(heater));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const value = Number(input.value);
    if (input.value === "" || !Number.isFinite(value) || value < 0) { input.focus(); return; }
    let confirmed = false;
    if (heater === "nozzle" && value > (ui.state?.high_nozzle ?? 250)) {
      if (!(await ask(`Heat the nozzle to ${value}°?`, "That's hotter than most filaments need. Make sure your hotend is rated for it.", `Heat to ${value}°`))) return;
      confirmed = true;
    }
    send({ action: "settemp", heater, value, confirmed });
    input.blur();
  };
  form.querySelector("[data-off]").onclick = () => send({ action: "settemp", heater, value: 0 });
});

function renderPresets(presets) {
  const box = $("presets");
  const sig = JSON.stringify(presets);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = "";
  presets.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "preset";
    b.innerHTML = `<strong></strong><small></small>`;
    b.querySelector("strong").textContent = p.name;
    b.querySelector("small").textContent = `${Math.round(p.nozzle)}° / ${Math.round(p.bed)}°`;
    b.title = `Nozzle ${Math.round(p.nozzle)}°, bed ${Math.round(p.bed)}°`;
    b.onclick = () => send({ action: "preheat", material: p.name });
    box.appendChild(b);
  });
}

function renderTools(s) {
  const multi = s.extruders > 1;
  $("tool-row").hidden = !multi;
  $("s-tools").hidden = !multi;
  if (!multi) return;
  const seg = $("tools");
  if (seg.childElementCount !== s.extruders) {
    seg.innerHTML = "";
    for (let i = 0; i < s.extruders; i++) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.textContent = `Nozzle ${i + 1}`;
      b.onclick = () => send({ action: "tool", tool: i });
      seg.appendChild(b);
    }
  }
  [...seg.children].forEach((b, i) => b.setAttribute("aria-checked", String(i === s.tool)));
  $("s-tools").innerHTML = "";
  Object.entries(s.tools || {}).sort().forEach(([k, [cur, tgt]]) => {
    const line = document.createElement("span");
    line.textContent = `Nozzle ${Number(k.slice(1)) + 1}: ${Math.round(cur)}°${tgt ? ` / ${Math.round(tgt)}°` : ""}`;
    $("s-tools").appendChild(line);
  });
}

// ---------- safety ----------
$("heaters-off").onclick = async () => {
  if (printing() && !(await ask("A print is running", "Turning the heaters off now will ruin the print.", "Turn heaters off"))) return;
  send({ action: "heaters_off" });
};
$("motors-off").onclick = async () => {
  if (printing() && !(await ask("A print is running", "Turning the motors off now will ruin the print.", "Turn motors off"))) return;
  send({ action: "motors_off" });
};
$("clear-all").onclick = async () => {
  if (printing() && !(await ask("Cancel the print?", "Clear all cancels the print, turns off heaters, fan and motors, and unloads the file.", "Clear all"))) return;
  send({ action: "clear_all" });
};

// ---------- lock ----------
$("lock").onclick = () => send({ action: "lock", on: !ui.state?.locked });

// ---------- state ----------
function renderState(s) {
  ui.state = s;
  const online = !!s.online;
  document.body.classList.toggle("online", online);
  document.body.classList.toggle("locked", !!s.locked);

  // top bar
  const conn = $("conn");
  conn.dataset.state = online ? "online" : s.connecting ? "connecting" : "offline";
  $("conn-text").textContent = online
    ? `Connected to ${s.port} at ${Number(s.baud).toLocaleString("en-US")}`
    : s.connecting ? "Connecting…" : "Not connected";
  const lockBtn = $("lock");
  lockBtn.hidden = !s.settings.lockbox && !s.locked;
  lockBtn.setAttribute("aria-pressed", String(!!s.locked));
  $("lock-text").textContent = s.locked ? "Locked" : "Lock";
  lockBtn.title = s.locked ? "Unlock the controls" : "Lock the controls";
  $("settings-btn").disabled = !!s.locked;

  // connection card
  const btn = $("connect");
  btn.textContent = online ? "Disconnect" : s.connecting ? "Cancel" : "Connect";
  btn.classList.toggle("primary", !(online || s.connecting));
  $("reset").disabled = !online;
  ["port", "rescan", "custom-port"].forEach((id) => { $(id).disabled = online || s.connecting; });
  $("baud").querySelectorAll("button").forEach((b) => { b.disabled = online || s.connecting; });
  $("conn-summary").hidden = !online;
  $("conn-summary-text").textContent = online ? `${s.port} at ${Number(s.baud).toLocaleString("en-US")}` : "";
  $("debug").checked = !!s.debug;

  // status strip
  $("strip").hidden = !s.settings.statuspanel;
  for (const kind of ["nozzle", "bed"]) {
    const cur = s.temps[kind], tgt = s.temps[kind + "_target"];
    const t = tempText(cur, tgt, online);
    $(`s-${kind}`).textContent = t.now;
    $(`s-${kind}-t`).textContent = online && tgt ? ` / ${Math.round(tgt)}°` : "";
    $(`${kind}-now`).textContent = online ? `${t.now} now${t.status === "heating" ? ", heating" : t.status === "cooling" ? ", cooling" : ""}` : "Not connected";
    const gauge = $(`g-${kind}`);
    gauge.parentElement.hidden = !s.settings.tempgauges;
    gauge.style.width = `${online && tgt ? Math.min(100, (cur / tgt) * 100) : 0}%`;
    const input = $(`${kind}-target`);
    if (!ui.editing.has(kind) && document.activeElement !== input) {
      input.value = online ? String(Math.round(tgt)) : "";
    }
  }
  renderJob(s);

  // controls that need a printer
  document.querySelectorAll("form.heater button, .preset, .tile, #tools button").forEach((el) => { el.disabled = !online; });
  document.querySelectorAll("form.heater input").forEach((el) => { el.disabled = !online; });

  renderPresets(s.presets || []);
  renderTools(s);
  $("graph-group").hidden = !s.settings.tempgraph;
  updateLegends(s);
  if (!$("settings-sheet").hidden) syncSettings(s);
}

// ---------- printing ----------
function duration(sec) {
  if (sec == null) return "—";
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec < 5 ? "Almost done" : "Under 1 min";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function filamentText(mm) {
  if (!mm) return "";
  return mm >= 1000 ? `${(mm / 1000).toFixed(2)} m of filament` : `${Math.round(mm)} mm of filament`;
}

function renderJob(s) {
  const job = s.job || {};
  const online = !!s.online;
  const active = !!job.active;
  const paused = !!s.paused;
  const running = active && !paused;

  // file row
  $("file-name").textContent = job.loading ? "Loading file…" : job.loaded ? job.name : "No file loaded";
  $("file-name").title = job.path || "";
  $("file-info").textContent = job.loading ? "Reading the G-code" : job.loaded
    ? [job.duration ? `About ${duration(job.duration)}` : "", filamentText(job.filament),
       job.layers ? `${job.layers} layers` : "", job.height ? `${job.height} mm tall` : ""].filter(Boolean).join(", ")
    : "Choose a G-code file exported from your slicer";
  $("file-clear").hidden = !job.loaded || active;
  $("choose-file").disabled = active || job.loading;

  // recent files
  const sel = $("recent");
  const sig = JSON.stringify(s.recent || []);
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = "";
    sel.add(new Option((s.recent || []).length ? "Recent files" : "No recent files", ""));
    (s.recent || []).forEach((f) => { const o = new Option(f.name, f.path); o.title = f.path; sel.add(o); });
  }
  sel.disabled = active || job.loading || !(s.recent || []).length;

  // buttons
  const printBtn = $("print-btn");
  printBtn.textContent = paused ? "Resume" : "Print";
  printBtn.hidden = running;
  printBtn.disabled = !online || job.loading || !(job.loaded || paused);
  $("pause-btn").hidden = !running;
  $("pause-btn").disabled = !online;
  $("restart-btn").hidden = !paused || s.sdprinting;
  $("cancel-btn").hidden = !active;

  // interrupted print
  const rec = job.recovery;
  $("recover").hidden = !rec;
  if (rec) {
    $("recover-text").textContent = `${rec.filename || "The print"} stopped at ${Math.round(rec.percent)}%, at a height of ${rec.z.toFixed(2)} mm.`;
    $("recover-btn").disabled = !online || active;
  }

  // status strip
  $("s-state").textContent = !online ? (s.connecting ? "Connecting…" : "Not connected")
    : job.loading ? "Loading file…" : paused ? "Paused" : active ? "Printing"
    : job.loaded ? "Ready to print" : "Idle";
  const lineText = active && job.line != null && job.total_lines ? `, line ${job.line.toLocaleString("en-US")} of ${job.total_lines.toLocaleString("en-US")}` : "";
  $("s-file").textContent = (active || job.loaded) && online ? (job.name || "") + lineText : "";
  const pct = active && job.progress != null ? job.progress : null;
  $("s-progress").textContent = pct == null ? "—" : `${Math.floor(pct * 100)}%`;
  $("s-bar").style.width = `${pct == null ? 0 : Math.min(100, pct * 100)}%`;
  $("s-left").textContent = active ? (paused ? "Paused" : duration(job.remaining)) : "—";
  $("s-height").textContent = active && job.z ? `${job.z.toFixed(2)} mm` : "—";
}

$("choose-file").onclick = () => {
  if (ui.nativeDialogs) send({ action: "pick_file" });
  else $("file-input").click();
};
$("file-input").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  addLog({ level: "info", text: `Uploading ${file.name}…` });
  const res = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
  if (!res.ok) addLog({ level: "error", text: `Couldn't open ${file.name}.` });
};
$("recent").onchange = (e) => {
  if (e.target.value) send({ action: "load_recent", path: e.target.value });
  e.target.value = "";
};
$("file-clear").onclick = () => send({ action: "clear_file" });
$("print-btn").onclick = () => send({ action: ui.state?.paused ? "resume" : "print" });
$("pause-btn").onclick = () => send({ action: "pause" });
$("restart-btn").onclick = async () => {
  if (await ask("Start the print over?", "The print starts again from the beginning. Clear the bed first.", "Start over", false)) {
    send({ action: "print" });
  }
};
$("cancel-btn").onclick = async () => {
  if (await ask("Cancel the print?", "The print stops here and can't be resumed. Heaters stay on and the file stays loaded, so you can start it again.", "Cancel print", true, "Keep printing")) {
    send({ action: "cancel" });
  }
};
$("recover-btn").onclick = () => send({ action: "recover" });
$("recover-discard").onclick = () => send({ action: "discard_recovery" });

// ---------- graph ----------
function addSample(sample) {
  ui.temps.push(sample);
  const cutoff = sample.t - HISTORY_SECONDS;
  while (ui.temps.length && ui.temps[0].t < cutoff) ui.temps.shift();
  drawGraphs();
}

function updateLegends(s) {
  const online = s.online;
  for (const kind of ["nozzle", "bed"]) {
    const cur = s.temps[kind], tgt = s.temps[kind + "_target"];
    $(`l-${kind}`).textContent = online ? `${Math.round(cur)}°${tgt ? ` / ${Math.round(tgt)}°` : ""}` : "—";
  }
}

let drawQueued = false;
function drawGraphs() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => {
    drawQueued = false;
    if (!$("graph-group").hidden) TempGraph.draw($("graph-small"), ui.temps, { seconds: 60 });
  });
}
window.addEventListener("resize", drawGraphs);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawGraphs);

$("graph-open").onclick = () => send({ action: "open_graph" });

// ---------- settings sheet ----------
function buildSettings() {
  const body = $("settings-body");
  body.innerHTML = "";
  for (const group of SETTINGS_LAYOUT) {
    const sec = document.createElement("section");
    sec.className = "settings-group";
    const h = document.createElement("h3");
    h.className = "group-title";
    h.textContent = group.title;
    const list = document.createElement("div");
    list.className = "list";
    for (const [name, label, help, unit = "°"] of group.items) {
      const info = ui.settingsInfo[name];
      if (!info) continue;
      const row = document.createElement("label");
      row.className = "row";
      const lab = document.createElement("span");
      lab.className = "row-label";
      lab.textContent = label;
      if (help) { const sm = document.createElement("small"); sm.textContent = help; lab.appendChild(sm); }
      row.appendChild(lab);
      let control;
      if (info.kind === "bool") {
        control = document.createElement("input");
        control.type = "checkbox";
        control.className = "switch";
        control.onchange = () => send({ action: "setting", name, value: control.checked ? "True" : "False" });
      } else if (name === "extruders") {
        control = document.createElement("span");
        control.className = "stepper";
        control.innerHTML = `<button type="button" aria-label="Fewer">−</button><output></output><button type="button" aria-label="More">+</button>`;
        const [minus, out, plus] = control.children;
        const change = (d) => {
          const v = Math.min(info.max || 5, Math.max(info.min || 1, Number(out.value || 1) + d));
          out.value = v;
          send({ action: "setting", name, value: String(v) });
        };
        minus.onclick = (e) => { e.preventDefault(); change(-1); };
        plus.onclick = (e) => { e.preventDefault(); change(1); };
      } else {
        control = document.createElement("span");
        control.className = "num-wrap";
        control.innerHTML = `<input class="num" type="number" min="0" max="400" step="1"><span class="unit"></span>`;
        control.querySelector(".unit").textContent = unit;
        const input = control.querySelector("input");
        input.setAttribute("aria-label", label);
        input.onchange = () => { if (input.value !== "") send({ action: "setting", name, value: String(Math.round(Number(input.value))) }); };
      }
      control.dataset.setting = name;
      row.appendChild(control);
      list.appendChild(row);
    }
    sec.append(h, list);
    body.appendChild(sec);
  }
}

function syncSettings(s) {
  document.querySelectorAll("#settings-body [data-setting]").forEach((el) => {
    const v = s.settings[el.dataset.setting];
    if (el.classList.contains("switch")) el.checked = !!v;
    else if (el.classList.contains("stepper")) el.querySelector("output").value = Math.max(1, Number(v) || 1);
    else {
      const input = el.querySelector("input");
      if (document.activeElement !== input) input.value = Math.round(Number(v));
    }
  });
}

$("settings-btn").onclick = () => {
  buildSettings();
  syncSettings(ui.state);
  $("settings-sheet").hidden = false;
  $("settings-close").focus();
};
$("settings-close").onclick = () => { $("settings-sheet").hidden = true; };

// Close sheets with Escape / click outside
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  $("settings-sheet").hidden = true;
  if (!$("confirm-sheet").hidden) $("confirm-cancel").click();
});
$("settings-sheet").addEventListener("click", (e) => { if (e.target.id === "settings-sheet") e.currentTarget.hidden = true; });

// ---------- console ----------
function addLog(entry) {
  const log = $("log");
  const empty = $("log-empty");
  if (empty) empty.remove();
  const p = document.createElement("p");
  p.className = "log-line";
  if (entry.level === "error" || entry.level === "critical") p.classList.add("error");
  else if (entry.level === "warning") p.classList.add("warning");
  else if (entry.text.startsWith("> ") || entry.text.startsWith("SENT:")) p.classList.add("send");
  p.textContent = entry.text;
  log.appendChild(p);
  while (log.childElementCount > MAX_LOG_LINES) log.firstElementChild.remove();
  if ($("autoscroll").checked) log.scrollTop = log.scrollHeight;
}

$("clear").onclick = () => { $("log").innerHTML = ""; send({ action: "clearlog" }); };
$("debug").onchange = (e) => send({ action: "debug", on: e.target.checked });

$("console-form").onsubmit = (e) => {
  e.preventDefault();
  const input = $("cmd");
  const line = input.value.trim();
  if (!line) return;
  send({ action: "command", line });
  if (ui.history[ui.history.length - 1] !== line) ui.history.push(line);
  ui.historyPos = -1;
  input.value = "";
};

$("cmd").onkeydown = (e) => {
  if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
  if (!ui.history.length) return;
  e.preventDefault();
  if (ui.historyPos === -1) { ui.draft = e.target.value; ui.historyPos = ui.history.length; }
  ui.historyPos += e.key === "ArrowUp" ? -1 : 1;
  ui.historyPos = Math.max(0, Math.min(ui.history.length, ui.historyPos));
  e.target.value = ui.historyPos === ui.history.length ? ui.draft : ui.history[ui.historyPos];
  if (ui.historyPos === ui.history.length) ui.historyPos = -1;
};

start().catch((err) => {
  addLog({ level: "error", text: "Couldn't start the interface: " + err });
});
