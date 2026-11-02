"""The printer engine: Pronterface's own console host (pronsole), unchanged,
with a thin layer that reports what happens to the web interface.

Everything that talks to the printer (connecting, sending G-code, reading
temperatures, printing, SD card, macros, settings file) is pronsole/printcore.
This file only:
  * runs typed commands one at a time on a worker thread,
  * forwards log messages and status to the interface,
  * adds the few commands that only existed in the classic window
    (button, setspeed, setflow),
  * replaces the terminal-only bits (y/n prompts, prompt printing).
"""

import collections
import logging
import queue
import re
import threading
import time
import traceback

from printrun import gcoder, pronsole
from printrun.settings import BooleanSetting, HiddenSetting, SpinSetting
from printrun.utils import parse_temperature_report

BAUD_RATES = [2400, 9600, 19200, 38400, 57600, 115200, 250000, 500000, 1000000]
AUTO_BAUDS = [115200, 250000]   # Marlin's usual speeds; Ender 5 Pro uses 115200
MAX_CUSTOM_BUTTONS = 64
HISTORY_SECONDS = 15 * 60       # longest graph range
HIGH_NOZZLE_TEMP = 250          # classic asks before going above this
MATERIALS = ("PLA", "PETG", "ABS")

# Settings the interface may change (anything else goes through `set` in the console)
EDITABLE_SETTINGS = {
    "monitor", "extruders", "tempgraph", "tempgauges", "statuspanel", "lockbox", "lockonstart",
    "heaters_off_on_exit", "dtr",
    "temperature_pla", "bedtemp_pla", "temperature_petg", "bedtemp_petg",
    "temperature_abs", "bedtemp_abs",
}

# Settings the classic Pronterface window stores in the same settings file.
# The engine doesn't use them yet, but knowing their names avoids
# "Unknown variable" warnings and keeps them intact.
CLASSIC_ONLY_SETTING = re.compile(r"^\s*set\s+(\S+)\s")


class _LogWriter:
    """Text written to the terminal (e.g. `help` output) goes to the console instead."""

    def __init__(self):
        self._buf = ""

    def write(self, text):
        self._buf += str(text)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.replace("\r", "").rstrip()
            if line and not set(line) <= set("=-"):
                logging.info(line)
        return len(text)

    def flush(self):
        pass


class WebHost(pronsole.pronsole):
    """pronsole, adapted to report to a web interface instead of a terminal."""

    def __init__(self, emit):
        self._emit = emit
        self._commands = queue.Queue()
        self._worker = None
        self._autoconnecting = False
        self.custombuttons = []
        self.quit_requested = False
        self.temp_history = collections.deque()
        self.fan = 0                        # percent, from M106/M107 sent to the printer
        self.tool_temps = {}                # {"T0": [current, target], ...} for multi-extruder printers
        self.locked = False
        self._confirmed = False
        super().__init__()
        self.stdout = _LogWriter()          # where `help` and friends write
        self._add_interface_settings()
        self.p.callback.send = self._sentcb
        # Classic code creates only the last folder level; make the whole path
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # The printer engine reports "online" through this callback
        self.p.callback.online = self.online
        self.p.callback.error = self.logError

    # -- terminal-only behaviour, replaced -----------------------------
    def confirm(self):
        # The classic console asked "y/n" in the terminal. The interface asks
        # its own questions before sending risky commands, so typed commands
        # that need confirmation are refused rather than guessed.
        if self._confirmed:
            return True
        self.log("This needs confirmation; use the button in the interface instead.")
        return False

    def _add_interface_settings(self):
        """Settings the classic window added on top of pronsole's (same names and defaults)."""
        add = self.settings._add
        add(BooleanSetting("monitor", True, "Monitor printer status",
                           "Regularly ask the printer for temperatures (needed for the graph and readouts)", "Printer"))
        add(SpinSetting("extruders", 0, 1, 5, "Extruders count", "Number of extruders", "Printer"))
        add(BooleanSetting("tempgraph", True, "Display temperature graph", "Display time-lapse temperature graph", "UI"))
        add(BooleanSetting("tempgauges", False, "Display temperature gauges",
                           "Show a bar under each temperature for how close it is to its target", "UI"))
        add(BooleanSetting("statuspanel", True, "Display big status panel",
                           "Show the large strip with temperatures and print progress", "UI"))
        add(BooleanSetting("lockbox", False, "Display interface lock",
                           "Show a lock button that disables most controls", "UI"))
        add(BooleanSetting("lockonstart", False, "Lock interface upon print start",
                           "If the lock is shown, lock the interface when a print starts", "UI"))
        add(BooleanSetting("heaters_off_on_exit", False, "Turn heaters off when closing",
                           "Switch the nozzle and bed heaters off when the app closes. Off by default: heaters are left as they are.", "Printer"))

    # -- temperatures, fan, tools ------------------------------------------
    def statuschecker(self):
        # Same loop as classic, honouring the "Monitor printer status" setting
        while self.statuscheck:
            self.statuschecker_inner(self.settings.monitor)

    def recvcb(self, l):
        super().recvcb(l)
        if "T:" in l or "B:" in l:
            temps = parse_temperature_report(l)
            if temps:
                self._record_temps(temps)

    def _record_temps(self, temps):
        for name, (cur, tgt) in temps.items():
            if name.startswith("T") and cur:
                key = "T0" if name == "T" else name
                old = self.tool_temps.get(key, [0.0, 0.0])
                self.tool_temps[key] = [float(cur), float(tgt) if tgt else old[1]]
        s = self.status
        sample = {"t": time.time(), "n": s.extruder_temp, "nt": s.extruder_temp_target,
                  "b": s.bed_temp, "bt": s.bed_temp_target, "f": self.fan}
        self.temp_history.append(sample)
        cutoff = sample["t"] - HISTORY_SECONDS
        while self.temp_history and self.temp_history[0]["t"] < cutoff:
            self.temp_history.popleft()
        self._emit({"type": "temps", "sample": sample})

    def _sentcb(self, command, gline):
        """Watch commands going to the printer (like classic Pronterface did)."""
        if not gline:
            return
        c = gline.command
        if c in ("M104", "M109", "M140", "M190"):
            value = gcoder.S(gline)
            if value is not None:
                if c in ("M140", "M190"):
                    self.status.bed_temp_target = float(value)
                elif "T" not in gline.raw.upper()[4:] or self.current_tool == 0:
                    self.status.extruder_temp_target = float(value)
                self._emit({"type": "state"})
        elif c == "M106":
            value = gcoder.S(gline)
            self.fan = round(min(255, 255 if value is None else value) / 255 * 100)
        elif c == "M107":
            self.fan = 0
        elif c.startswith("T") and c[1:].isdigit():
            self.current_tool = int(c[1:])

    def _extruder_count(self):
        return max(1, int(self.settings.extruders or 1))

    def _run_confirmed(self, fn):
        self._confirmed = True
        try:
            fn()
        finally:
            self._confirmed = False

    def set_temp(self, heater, value, confirmed=False):
        try:
            value = float(value)
        except (TypeError, ValueError):
            self.logError("You must enter a temperature.")
            return
        text = "%g" % value
        if heater == "bed":
            self.run(lambda: self.do_bedtemp(text))
        elif confirmed:
            self.run(lambda: self._run_confirmed(lambda: self.do_settemp(text)))
        else:
            self.run(lambda: self.do_settemp(text))

    def presets(self):
        st = self.settings
        return [{"name": m,
                 "nozzle": float(getattr(st, "temperature_" + m.lower())),
                 "bed": float(getattr(st, "bedtemp_" + m.lower()))} for m in MATERIALS]

    def preheat(self, material):
        preset = next((p for p in self.presets() if p["name"] == material), None)
        if preset is None:
            self.logError("Unknown material %s" % material)
            return

        def go():
            if not self.p.online:
                self.logError("Printer is not online.")
                return
            self.log("Preheating for %s" % material)
            self._run_confirmed(lambda: self.do_settemp("%g" % preset["nozzle"]))
            self.do_bedtemp("%g" % preset["bed"])
        self.run(go)

    def _all_heaters_off(self):
        if self._extruder_count() > 1:
            for tool in range(self._extruder_count()):
                self.p.send_now("M104 T%d S0" % tool)
        self.do_settemp("0")
        self.do_bedtemp("0")

    def heaters_off(self):
        def go():
            if not self.p.online:
                self.logError("Printer is not online.")
                return
            self._all_heaters_off()
            self.log("Heaters off.")
        self.run(go)

    def motors_off(self):
        def go():
            if not self.p.online:
                self.logError("Printer is not online.")
                return
            self.p.send_now("M84")
            self.log("Motors off.")
        self.run(go)

    def clear_all(self):
        """Cancel the print, heaters + fan + motors off, unload the file."""
        def go():
            if self.p.online:
                if self.sdprinting:
                    self.p.send_now("M25")
                elif self.p.printing or self.paused:
                    self.p.cancelprint()
                self.paused = False
                self.sdprinting = False
                self._all_heaters_off()
                self.p.send_now("M107")
                self.p.send_now("M84")
            self.fgcode = None
            self.filename = None
            self.log("Cleared: print cancelled, heaters, fan and motors off, file unloaded.")
        self.run(go)

    def set_tool(self, tool):
        self.run(lambda: self.do_tool(str(int(tool))))

    def set_locked(self, locked):
        self.locked = bool(locked)
        self._emit({"type": "state"})

    def startcb(self, resuming=False):
        super().startcb(resuming)
        if not resuming and self.settings.lockbox and self.settings.lockonstart:
            self.set_locked(True)

    def set_setting(self, name, value):
        if name not in EDITABLE_SETTINGS:
            self.logError("That setting can't be changed from here.")
            return
        self.run(lambda: self.do_set("%s %s" % (name, value)))

    def promptf(self):
        return ""

    def write_prompt(self):
        pass

    def online(self):
        self.log("Printer is now online.")
        self._autoconnecting = False
        self._emit({"type": "state"})

    # -- settings file -------------------------------------------------
    def load_rc(self, rc_file):
        """Register settings the classic window uses before loading the file,
        so they are kept as-is instead of logging 'Unknown variable'."""
        try:
            with rc_file.expanduser().open("r", encoding="utf-8") as rc:
                for line in rc:
                    m = CLASSIC_ONLY_SETTING.match(line)
                    if m and not hasattr(self.settings, m.group(1)):
                        self.settings._add(HiddenSetting(m.group(1), ""))
        except OSError:
            pass
        super().load_rc(rc_file)

    # -- commands that only existed in the classic window ---------------
    def do_button(self, argstr):
        """Same syntax as classic Pronterface: button <num> "title" [/c "colour"] command"""
        def nextarg(rest):
            rest = rest.lstrip()
            if rest.startswith('"'):
                return rest[1:].split('"', 1)
            return rest.split(None, 1)
        try:
            num, argstr = nextarg(argstr)
            num = int(num)
            title, argstr = nextarg(argstr)
        except ValueError:
            self.log('Defines custom button. Usage: button <num> "title" [/c "colour"] command')
            return
        colour = None
        try:
            c1, c2 = nextarg(argstr)
            if c1 == "/c":
                colour, argstr = nextarg(c2)
        except ValueError:
            pass
        if num < 0 or num >= MAX_CUSTOM_BUTTONS:
            self.log("Custom button number should be between 0 and 63")
            return
        while num >= len(self.custombuttons):
            self.custombuttons.append(None)
        self.custombuttons[num] = {"title": title, "command": argstr.strip(), "colour": colour}
        if not self.processing_rc:
            self._emit({"type": "buttons"})

    def help_button(self):
        self.log('Defines custom button. Usage: button <num> "title" [/c "colour"] command')

    def _set_factor(self, gcode, what, l):
        try:
            value = int(float(l.strip()))
        except ValueError:
            self.logError("You must enter a %s." % what)
            return
        if self.p.online:
            self.p.send_now("%s S%d" % (gcode, value))
            self.log("Setting print %s factor to %d%%." % (what, value))
        else:
            self.logError("Printer is not online.")

    def do_setspeed(self, l):
        self._set_factor("M220", "speed", l)

    def help_setspeed(self):
        self.log("Set print speed factor in percent: setspeed <percent>")

    def do_setflow(self, l):
        self._set_factor("M221", "flow", l)

    def help_setflow(self):
        self.log("Set print flow factor in percent: setflow <percent>")

    def do_exit(self, l):
        # Closing never touches the heaters (same as our classic change).
        if (self.p.printing or self.sdprinting) and l.strip() != "force":
            self.log("A print is running. Close the window to quit (it will ask first).")
            return
        self.quit_requested = True
        self._emit({"type": "quit"})

    # -- running commands ----------------------------------------------
    def start(self):
        self._worker = threading.Thread(target=self._run_commands, name="command worker", daemon=True)
        self._worker.start()

    def submit(self, line):
        """Queue a typed command (G-code or Pronterface command)."""
        self._commands.put(line)

    def _run_commands(self):
        while True:
            line = self._commands.get()
            if line is None:
                return
            try:
                if callable(line):
                    line()
                else:
                    line = self.precmd(line)
                    self.onecmd(line)
            except SystemExit:
                pass
            except Exception:
                self.logError(traceback.format_exc())
            self._emit({"type": "state"})

    def run(self, fn):
        """Run a function on the command worker (keeps commands in order)."""
        self._commands.put(fn)

    # -- connection helpers ------------------------------------------------
    def connect(self, port, baud):
        if port in (None, "", "auto"):
            self.run(self._autoconnect)
            return
        self.run(lambda: self.do_connect("%s %d" % (port, int(baud))))

    def _autoconnect(self):
        """Try the saved port first, then every port, at Marlin's usual speeds."""
        ports = self.scanserial()
        saved = self.settings.port
        candidates = ([saved] if saved in ports else []) + [p for p in ports if p != saved]
        if not candidates:
            self.logError("No printer found. Check the USB cable and that the printer is on.")
            return
        bauds = [self.settings.baudrate] + [b for b in AUTO_BAUDS if b != self.settings.baudrate]
        self._autoconnecting = True
        for port in candidates:
            for baud in bauds:
                self.log("Trying %s at %d..." % (port, baud))
                self.do_connect("%s %d" % (port, baud))
                deadline = time.time() + 8
                while time.time() < deadline and not self.p.online:
                    time.sleep(0.2)
                if self.p.online:
                    self._autoconnecting = False
                    return
                self.p.disconnect()
        self._autoconnecting = False
        self.logError("Couldn't find the printer automatically. Pick the port and speed by hand.")

    def disconnect(self):
        self.run(lambda: self.do_disconnect(""))

    def reset(self):
        self.run(lambda: self.do_reset(""))

    def set_debug(self, on):
        self.p.loud = bool(on)
        self.log("Debug communications %s." % ("on" if on else "off"))

    # -- history (shared with classic Pronterface) ---------------------
    def read_history(self):
        try:
            lines = self.history_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [l for l in lines if l.strip()][-200:]

    def add_history(self, line):
        try:
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    # -- status for the interface --------------------------------------
    def snapshot(self):
        p, s = self.p, self.status
        connected = bool(p.printer) and getattr(p.printer, "is_connected", False)
        return {
            "online": bool(p.online),
            "connecting": bool(connected and not p.online) or self._autoconnecting,
            "port": p.port if connected else None,
            "baud": p.baud if connected else None,
            "printing": bool(p.printing),
            "paused": bool(self.paused),
            "sdprinting": bool(self.sdprinting),
            "filename": self.filename,
            "debug": bool(p.loud),
            "tool": self.current_tool,
            "temps": {
                "nozzle": s.extruder_temp, "nozzle_target": s.extruder_temp_target,
                "bed": s.bed_temp, "bed_target": s.bed_temp_target,
            },
            "fan": self.fan,
            "tools": self.tool_temps if self._extruder_count() > 1 else {},
            "extruders": self._extruder_count(),
            "locked": self.locked,
            "presets": self.presets(),
            "high_nozzle": HIGH_NOZZLE_TEMP,
            "settings": {
                "port": self.settings.port, "baudrate": self.settings.baudrate,
                **{name: getattr(self.settings, name) for name in sorted(EDITABLE_SETTINGS)},
            },
        }

    def settings_info(self):
        """Labels/help/type for the settings the interface can edit."""
        info = {}
        for name in sorted(EDITABLE_SETTINGS):
            st = getattr(self.settings, "_" + name)
            kind = "bool" if isinstance(st, BooleanSetting) else "number" if isinstance(st, SpinSetting) else "text"
            info[name] = {"label": (st.label or name).rstrip(":"), "help": st.help or "", "kind": kind,
                          "group": st.group or "", "min": getattr(st, "min", None), "max": getattr(st, "max", None)}
        return info

    def shutdown(self):
        """Disconnect cleanly. Heaters are left as they are, unless the
        "Turn heaters off when closing" setting is on."""
        try:
            if self.settings.heaters_off_on_exit and self.p.online:
                self.log("Turning heaters off (setting: turn heaters off when closing).")
                self._all_heaters_off()
                time.sleep(0.6)   # let the commands reach the printer
            self.p.disconnect()
        finally:
            self.kill()
            self._commands.put(None)


class InterfaceLogHandler(logging.Handler):
    """Sends every log message (Pronterface's and the printer's) to the interface."""

    def __init__(self, emit):
        super().__init__(logging.INFO)
        self._emit = emit

    def emit(self, record):
        try:
            text = record.getMessage().replace("\r", "").rstrip()
            # The console already echoes typed commands as "> ..."
            if text.startswith("SENDING:"):
                return
            if text:
                self._emit({"type": "log", "level": record.levelname.lower(),
                            "text": text, "time": record.created})
        except Exception:
            pass
