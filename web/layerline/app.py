"""Layerline launcher: starts the engine and the local server, then
opens the interface in its own app window (or the browser as a fallback)."""

import argparse
import io
import logging
import os
import sys
import threading
import time
import webbrowser

from . import __version__


class _NullWriter(io.TextIOBase):
    """Windowed .exe files have no console: give print() somewhere harmless to go."""
    def write(self, s):
        return len(s)


def _ensure_streams():
    if sys.stdout is None:
        sys.stdout = _NullWriter()
    if sys.stderr is None:
        sys.stderr = _NullWriter()


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-a", "--autoconnect", action="store_true",
                        help="connect to the printer on startup")
    parser.add_argument("--browser", action="store_true",
                        help="open in the web browser instead of an app window")
    parser.add_argument("--port", type=int, default=8086,
                        help="local port for the interface (default 8086)")
    parser.add_argument("--selftest", metavar="RESULT_FILE",
                        help="run a quick self-check against a simulated printer and exit")
    parser.add_argument("--version", action="store_true")
    return parser.parse_known_args(argv)


def build(port, rest, emit_target=None):
    """Create engine + server. `rest` are Pronterface's own options (-c, -e, -v, file)."""
    from .engine import WebHost, InterfaceLogHandler
    from .server import Server

    server = Server(port=port)
    engine = WebHost(server.emit)
    handler = InterfaceLogHandler(server.emit)
    root = logging.getLogger()
    root.addHandler(handler)
    engine.parse_cmdline(rest)          # loads the settings file, -c / -e / file
    if handler not in root.handlers:     # Pronterface resets log handlers while loading
        root.addHandler(handler)
    engine.start()
    url = server.start(engine)
    return engine, server, url


def selftest(result_file, rest):
    """Proves the packaged app works: engine + server + printer talk."""
    import json
    import urllib.request
    from .fake_printer import FakePrinter
    lines, ok = [], True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        lines.append("%s %s" % ("PASS" if cond else "FAIL", name))

    try:
        fp = FakePrinter()
        engine, server, url = build(0, rest)
        data = json.load(urllib.request.urlopen(url.replace("/?", "/api/initial?"), timeout=10))
        check("web server answers", "state" in data)
        page = urllib.request.urlopen(url, timeout=10).read().decode()
        check("interface page served", "Layerline" in page)
        graph = urllib.request.urlopen(url.replace("/?", "/graph?"), timeout=10).read().decode()
        check("graph window page served", "Temperature graph" in graph)
        engine.connect(fp.address, 115200)
        t = time.time()
        while not engine.p.online and time.time() - t < 15:
            time.sleep(0.1)
        check("connects to printer", engine.p.online)
        engine.submit("M104 S200")
        t = time.time()
        while engine.status.extruder_temp < 60 and time.time() - t < 20:
            time.sleep(0.2)
        check("reads temperatures", engine.status.extruder_temp >= 60)
        engine.submit("M104 S0")
        time.sleep(0.5)
        engine.shutdown()
        fp.stop()
        try:
            import webview  # noqa: F401
            check("app window library present", True)
        except Exception as e:  # pragma: no cover
            check("app window library present (%s)" % e, sys.platform != "win32")
    except Exception as e:
        import traceback
        lines.append("FAIL crashed: " + traceback.format_exc())
        ok = False
    with open(result_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n" + ("ALL PASSED" if ok else "FAILED") + "\n")
    return 0 if ok else 1


def main(argv=None):
    _ensure_streams()
    opts, rest = parse_args(sys.argv[1:] if argv is None else argv)
    if opts.version:
        print("Layerline", __version__)
        return 0
    if opts.selftest:
        return selftest(opts.selftest, rest)

    engine, server, url = build(opts.port, rest)
    if opts.autoconnect:
        engine.connect("auto", None)

    window = None
    quit_event = threading.Event()

    def request_quit():
        quit_event.set()
        if window is not None:
            window.destroy()

    server.on_quit = request_quit

    webview = None
    if not opts.browser:
        try:
            import webview
        except Exception as e:
            logging.warning("App window unavailable (%s); opening in the browser instead." % e)
            webview = None

    if webview is not None:
        window = webview.create_window("Layerline", url, width=1280, height=820,
                                       min_size=(900, 600), background_color="#F2F2F7")
        extra_windows = {}

        def open_window(name):
            """Open (or bring back) a separate app window, e.g. the temperature graph."""
            existing = extra_windows.get(name)
            if existing is not None:
                try:
                    existing.restore()
                    existing.show()
                    return
                except Exception:
                    extra_windows.pop(name, None)
            if name == "graph":
                w = webview.create_window("Temperature graph", server.page_url("/graph"),
                                          width=1040, height=680, min_size=(520, 380),
                                          background_color="#F2F2F7")
                w.events.closed += lambda: extra_windows.pop(name, None)
                extra_windows[name] = w

        def close_extra_windows():
            for w in list(extra_windows.values()):
                try:
                    w.destroy()
                except Exception:
                    pass
            extra_windows.clear()

        def pick_file(start_dir):
            """Windows' own Open dialog, starting in the last folder used."""
            result = window.create_file_dialog(
                webview.OPEN_DIALOG, directory=start_dir or "",
                file_types=("G-code files (*.gcode;*.gco;*.g;*.nc)", "All files (*.*)"))
            if result:
                return result[0] if isinstance(result, (list, tuple)) else result
            return None

        server.on_open_window = open_window
        server.on_pick_file = pick_file
        window.events.closed += close_extra_windows

        def on_closing():
            # Don't close mid-print without asking (the page asks; this cancels the close)
            if (engine.p.printing or engine.sdprinting) and not quit_event.is_set():
                server.emit({"type": "confirm_close"})
                return False
            return True

        window.events.closing += on_closing
        webview.start(private_mode=False)
    else:
        webbrowser.open(url)
        print("Layerline is running at", url)
        try:
            while not quit_event.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass

    engine.shutdown()   # disconnects; heaters are left as they are
    return 0


if __name__ == "__main__":
    sys.exit(main())
