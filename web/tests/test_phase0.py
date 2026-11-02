"""Phase 0 tests: engine + server against the simulated printer.

Run from the repository folder:  python -m pytest web/tests
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", ".."), os.path.join(HERE, "..")]

from layerline.fake_printer import FakePrinter  # noqa: E402


def wait(cond, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    from layerline.app import build
    printer = FakePrinter()
    engine, server, url = build(0, [])
    yield printer, engine, server, url
    engine.shutdown()
    printer.stop()


def test_connects_and_reads_temps(rig):
    printer, engine, server, url = rig
    engine.connect(printer.address, 115200)
    assert wait(lambda: engine.p.online)
    engine.submit("M104 S200")
    assert wait(lambda: engine.status.extruder_temp > 60, 20)
    assert engine.snapshot()["temps"]["nozzle_target"] == 200


def test_console_commands(rig):
    printer, engine, server, url = rig
    before = len(printer.received)
    engine.submit("M115")
    engine.submit("setspeed 90")
    engine.submit("setflow 105")
    engine.submit('button 3 "Home all" /c "#ff0000" G28')
    assert wait(lambda: "M221 S105" in printer.received[before:])
    assert "M115" in printer.received[before:]
    assert "M220 S90" in printer.received[before:]
    assert engine.custombuttons[3] == {"title": "Home all", "command": "G28", "colour": "#ff0000"}


def test_help_reaches_console(rig):
    printer, engine, server, url = rig
    n = len(server.backlog)
    engine.submit("help")
    assert wait(lambda: any("Documented commands" in e["text"] for e in server.backlog[n:]), 5)


def test_page_needs_access_key(rig):
    printer, engine, server, url = rig
    bare = url.split("?")[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bare + "api/initial", timeout=5)
    assert e.value.code == 403
    data = json.load(urllib.request.urlopen(url.replace("/?", "/api/initial?"), timeout=5))
    assert data["state"]["online"] is True
    assert 115200 in data["bauds"]


def test_server_only_listens_on_this_pc(rig):
    printer, engine, server, url = rig
    assert url.startswith("http://127.0.0.1:")


def test_disconnect_leaves_heaters_alone(rig):
    printer, engine, server, url = rig
    before = len(printer.received)
    engine.disconnect()
    assert wait(lambda: not engine.p.online)
    sent = printer.received[before:]
    assert not any(c.startswith(("M104 S0", "M140 S0")) for c in sent)


def test_exit_refused_while_printing(rig):
    printer, engine, server, url = rig
    engine.quit_requested = False
    engine.p.printing = True
    try:
        engine.do_exit("")
        assert engine.quit_requested is False
    finally:
        engine.p.printing = False
