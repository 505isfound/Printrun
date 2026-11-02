"""Phase 2 tests: printing, against a simulated printer that takes time per move."""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", ".."), os.path.join(HERE, ".."), HERE]

from layerline.fake_printer import FakePrinter  # noqa: E402
from sample_gcode import write_sample  # noqa: E402


def wait(cond, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def moves(printer, since=0):
    return [c for c in printer.received[since:] if c.startswith("G1 X")]


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    home = tmp_path_factory.mktemp("home2")
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    from layerline.app import build
    printer = FakePrinter(move_delay=0.03)
    engine, server, url = build(0, [])
    engine.connect(printer.address, 115200)
    assert wait(lambda: engine.p.online)
    gfile = write_sample(str(home / "cube.gcode"))
    yield printer, engine, gfile
    engine.shutdown()
    printer.stop()


def job(engine):
    return engine.snapshot()["job"]


def test_load_file_and_info(rig):
    printer, engine, gfile = rig
    engine.load_file(gfile)
    assert wait(lambda: job(engine)["loaded"])
    info = job(engine)
    assert info["name"] == "cube.gcode"
    assert info["layers"] >= 20
    assert info["filament"] == pytest.approx(96.0, abs=0.5)    # 20 layers x 12 moves x 0.4 mm
    assert info["height"] == pytest.approx(4.0)      # printed height, not the final travel move
    assert info["size"] > 0
    assert engine.recent_files()[0] == os.path.abspath(gfile)
    assert engine.settings.last_file_path == os.path.dirname(os.path.abspath(gfile))


def test_print_progress_pause_resume_finish(rig):
    printer, engine, gfile = rig
    n = len(printer.received)
    engine.print_file()
    assert wait(lambda: job(engine).get("progress", 0) > 0.15, 10)
    info = job(engine)
    assert info["active"] and engine.snapshot()["printing"]
    assert info["z"] > 0
    assert info["remaining"] is not None

    engine.pause_print()
    assert wait(lambda: engine.paused, 5)
    time.sleep(0.4)
    count = len(moves(printer, n))
    time.sleep(1.0)
    assert len(moves(printer, n)) == count, "nothing should be sent while paused"
    assert not any(c.startswith(("M104 S0", "M140 S0")) for c in printer.received[n:])

    engine.resume_print()
    assert wait(lambda: len(moves(printer, n)) > count, 5)
    assert wait(lambda: not job(engine)["active"], 30)
    assert len(moves(printer, n)) >= 240
    assert engine.recovery is None


def test_restart_from_pause(rig):
    printer, engine, gfile = rig
    engine.print_file()
    assert wait(lambda: job(engine).get("progress", 0) > 0.3, 15)
    engine.pause_print()
    assert wait(lambda: engine.paused, 5)
    engine.print_file()             # "Restart"
    assert wait(lambda: engine.snapshot()["printing"], 5)
    assert wait(lambda: job(engine).get("progress", 1) < 0.2, 5)
    engine.cancel_print()
    assert wait(lambda: not job(engine)["active"], 10)


def test_cancel_keeps_heaters_and_file(rig):
    printer, engine, gfile = rig
    engine.set_temp("nozzle", 200)
    engine.print_file()
    assert wait(lambda: job(engine).get("progress", 0) > 0.1, 10)
    n = len(printer.received)
    engine.cancel_print()
    assert wait(lambda: not job(engine)["active"], 10)
    time.sleep(0.5)
    count = len(moves(printer))
    time.sleep(0.6)
    assert len(moves(printer)) == count, "print stopped"
    assert not any(c.startswith(("M104 S0", "M140 S0")) for c in printer.received[n:])
    assert job(engine)["loaded"], "file stays loaded so it can be restarted"


def test_cant_load_or_clear_while_printing(rig):
    printer, engine, gfile = rig
    engine.print_file()
    assert wait(lambda: job(engine)["active"], 5)
    engine.clear_file()
    engine.load_file(gfile)
    time.sleep(0.8)
    assert job(engine)["loaded"] and job(engine)["active"]
    engine.cancel_print()
    assert wait(lambda: not job(engine)["active"], 10)
    engine.clear_file()
    assert wait(lambda: not job(engine)["loaded"], 5)
    engine.load_file(gfile)
    assert wait(lambda: job(engine)["loaded"], 10)


def test_progress_on_printer_screen(rig):
    printer, engine, gfile = rig
    engine.set_setting("display_progress_on_printer", "True")
    engine.set_setting("printer_progress_update_interval", "1")
    assert wait(lambda: engine.settings.display_progress_on_printer, 5)
    n = len(printer.received)
    engine.print_file()
    assert wait(lambda: any(c.startswith("M117 ") and "Est" in c for c in printer.received[n:]), 15)
    msg = next(c for c in printer.received[n:] if c.startswith("M117 "))
    assert ":" not in msg
    engine.cancel_print()
    assert wait(lambda: not job(engine)["active"], 10)
    engine.set_setting("display_progress_on_printer", "False")


def test_recover_after_disconnect(rig):
    printer, engine, gfile = rig
    engine.print_file()
    assert wait(lambda: job(engine).get("progress", 0) > 0.3, 15)
    engine.disconnect()
    assert wait(lambda: not engine.p.online, 10)
    rec = job(engine)["recovery"]
    assert rec and rec["z"] > 0 and 20 < rec["percent"] < 100
    engine.connect(printer.address, 115200)
    assert wait(lambda: engine.p.online, 10)
    n = len(printer.received)
    engine.recover_print()
    assert wait(lambda: engine.snapshot()["printing"], 5)
    assert wait(lambda: not job(engine)["active"], 30)
    sent = [c for c in printer.received[n:] if not c.startswith(("M105", "M110"))]
    first_move = next(i for i, c in enumerate(sent) if c.startswith("G1 X"))
    # Z is restored and X/Y homed before any print move goes out
    assert sent[0].startswith("G92 Z")
    assert "G28 X Y" in sent[:first_move]
    # resumed from the middle, not the start
    resumed = moves(printer, n)
    assert 0 < len(resumed) < 240


def test_recover_after_cable_pull(rig):
    printer, engine, gfile = rig
    engine.print_file()
    assert wait(lambda: job(engine).get("progress", 0) > 0.2, 15)
    printer.drop_connection()          # like yanking the USB cable
    assert wait(lambda: not engine.p.online, 30)
    assert job(engine)["recovery"] is not None
    engine.connect(printer.address, 115200)
    assert wait(lambda: engine.p.online, 10)
    engine.discard_recovery()
    assert job(engine)["recovery"] is None
