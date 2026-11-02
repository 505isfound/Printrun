// Pronterface Web interface (Phase 0: connection, temperatures, console)
"use strict";

const $ = (id) => document.getElementById(id);
const COMMON_BAUDS = [115200, 250000];
const MAX_LOG_LINES = 3000;

const ui = {
  state: null,
  ports: [],
  bauds: [],
  baud: 115200,
  history: [],
  historyPos: -1,
  draft: "",
  ws: null,
  touchedPort: false,
};

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
    else if (msg.type === "ports") { ui.ports = msg.ports; renderPorts(); }
    else if (msg.type === "confirm_close") $("close-sheet").hidden = false;
  };
}

async function start() {
  const res = await fetch("/api/initial");
  const data = await res.json();
  ui.ports = data.ports;
  ui.bauds = data.bauds;
  ui.history = data.history || [];
  ui.baud = data.state.settings.baudrate || 115200;
  renderPorts(data.state.settings.port);
  renderBauds();
  data.log.forEach(addLog);
  renderState(data.state);
  openSocket();
}

// ---------- printer card ----------
function renderPorts(preferred) {
  const sel = $("port");
  const keep = preferred ?? sel.value;
  sel.innerHTML = "";
  const add = (value, label) => sel.add(new Option(label, value));
  add("auto", "Find automatically");
  ui.ports.forEach((p) => add(p, p));
  add("other", "Other…");
  if (keep && keep !== "auto" && keep !== "other" && !ui.ports.includes(keep)) {
    // Saved port isn't plugged in right now (or is a network address)
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
    const checked = label === "Other" ? isOther : Number(label) === ui.baud;
    b.setAttribute("aria-checked", String(checked));
    b.onclick = () => {
      if (label === "Other") {
        if (COMMON_BAUDS.includes(ui.baud)) ui.baud = 57600;
      } else ui.baud = Number(label);
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
$("dtr").onchange = (e) => send({ action: "set", name: "dtr", value: e.target.checked ? "True" : "False" });
$("reset").onclick = () => send({ action: "reset" });

$("connect").onclick = () => {
  const s = ui.state;
  if (s && (s.online || s.connecting)) { send({ action: "disconnect" }); return; }
  let port = $("port").value;
  if (port === "other") {
    port = $("custom-port").value.trim();
    if (!port) { $("custom-port").focus(); return; }
  }
  send({ action: "connect", port, baud: ui.baud });
};

// ---------- state ----------
function tempText(now, target) {
  if (!ui.state || !ui.state.online) return { now: "—", sub: "Not connected", status: "" };
  const n = Math.round(now);
  const t = Math.round(target);
  if (!t) return { now: `${n}°`, sub: n > 45 ? "Off, cooling down" : "Off", status: "off" };
  if (n < t - 3) return { now: `${n}°`, sub: `Heating to ${t}°`, status: "heating" };
  if (n > t + 3) return { now: `${n}°`, sub: `Cooling to ${t}°`, status: "cooling" };
  return { now: `${n}°`, sub: `Holding ${t}°`, status: "holding" };
}

function renderState(s) {
  ui.state = s;
  const conn = $("conn");
  const stateName = s.online ? "online" : s.connecting ? "connecting" : "offline";
  conn.dataset.state = stateName;
  $("conn-text").textContent = s.online
    ? `Connected to ${s.port} at ${Number(s.baud).toLocaleString("en-US")}`
    : s.connecting ? "Connecting…" : "Not connected";

  const btn = $("connect");
  btn.textContent = s.online ? "Disconnect" : s.connecting ? "Cancel" : "Connect";
  btn.classList.toggle("primary", !(s.online || s.connecting));
  $("reset").disabled = !s.online;
  ["port", "rescan", "custom-port"].forEach((id) => { $(id).disabled = s.online || s.connecting; });
  $("baud").querySelectorAll("button").forEach((b) => { b.disabled = s.online || s.connecting; });

  $("dtr").checked = !!s.settings.dtr;
  $("debug").checked = !!s.debug;

  for (const kind of ["nozzle", "bed"]) {
    const t = tempText(s.temps[kind], s.temps[kind + "_target"]);
    $(`${kind}-now`).textContent = t.now;
    const sub = $(`${kind}-sub`);
    sub.textContent = t.sub;
    sub.dataset.status = t.status;
  }
}

// ---------- console ----------
function addLog(entry) {
  const log = $("log");
  const empty = $("log-empty");
  if (empty) empty.remove();
  const p = document.createElement("p");
  p.className = "log-line";
  if (entry.level === "error" || entry.level === "critical") p.classList.add("error");
  else if (entry.level === "warning") p.classList.add("warning");
  else if (entry.text.startsWith("> ") || entry.text.startsWith("SENDING:") || entry.text.startsWith("SENT:")) p.classList.add("send");
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

// ---------- closing mid-print ----------
$("close-cancel").onclick = () => { $("close-sheet").hidden = true; };
$("close-confirm").onclick = () => { $("close-sheet").hidden = true; send({ action: "quit" }); };

start().catch((err) => {
  addLog({ level: "error", text: "Couldn't start the interface: " + err });
});
