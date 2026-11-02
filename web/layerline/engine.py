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

import logging
import queue
import re
import threading
import time
import traceback

from printrun import pronsole
from printrun.settings import HiddenSetting

BAUD_RATES = [2400, 9600, 19200, 38400, 57600, 115200, 250000, 500000, 1000000]
AUTO_BAUDS = [115200, 250000]   # Marlin's usual speeds; Ender 5 Pro uses 115200
MAX_CUSTOM_BUTTONS = 64

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
        super().__init__()
        self.stdout = _LogWriter()          # where `help` and friends write
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
        self.log("This needs confirmation; use the button in the interface instead.")
        return False

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
            "settings": {
                "port": self.settings.port, "baudrate": self.settings.baudrate,
                "dtr": bool(self.settings.dtr),
            },
        }

    def shutdown(self):
        """Disconnect cleanly. Heaters are left exactly as they are."""
        try:
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
