"""Phase 1 tests: temperatures & safety, against the simulated printer."""
import os
import sys
import time

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


def sent_since(printer, n):
    return [c for c in printer.received[n:] if c != "M105"]


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    home = tmp_path_factory.mktemp("home1")
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(home / ".cache")
    from layerline.app import build
    printer = FakePrinter()
    engine, server, url = build(0, [])
    engine.connect(printer.address, 115200)
    assert wait(lambda: engine.p.online)
    yield printer, engine, server
    engine.shutdown()
    printer.stop()


def test_set_nozzle_and_bed(rig):
    printer, engine, server = rig
    n = len(printer.received)
    engine.set_temp("nozzle", 205)
    engine.set_temp("bed", 65)
    assert wait(lambda: {"M104 S205", "M140 S65"} <= set(sent_since(printer, n)))
    # target shows immediately, before the next temperature report
    assert wait(lambda: engine.status.extruder_temp_target == 205, 3)


def test_high_nozzle_needs_confirmation(rig):
    printer, engine, server = rig
    n = len(printer.received)
    engine.set_temp("nozzle", 260)
    time.sleep(1)
    assert "M104 S260" not in sent_since(printer, n)
    engine.set_temp("nozzle", 260, confirmed=True)
    assert wait(lambda: "M104 S260" in sent_since(printer, n))


def test_preheat_uses_settings(rig):
    printer, engine, server = rig
    n = len(printer.received)
    engine.preheat("PETG")
    assert wait(lambda: {"M104 S235", "M140 S75"} <= set(sent_since(printer, n)))
    engine.set_setting("temperature_pla", "210")
    assert wait(lambda: engine.settings.temperature_pla == 210)
    n = len(printer.received)
    engine.preheat("PLA")
    assert wait(lambda: {"M104 S210", "M140 S60"} <= set(sent_since(printer, n)))


def test_history_records_samples(rig):
    printer, engine, server = rig
    assert wait(lambda: len(engine.temp_history) >= 2, 10)
    sample = engine.temp_history[-1]
    assert set(sample) == {"t", "n", "nt", "b", "bt", "f"}


def test_fan_tracked_from_sent_commands(rig):
    printer, engine, server = rig
    engine.submit("M106 S128")
    assert wait(lambda: engine.fan == 50)
    engine.submit("M107")
    assert wait(lambda: engine.fan == 0)


def test_heaters_off_and_motors_off(rig):
    printer, engine, server = rig
    n = len(printer.received)
    engine.heaters_off()
    engine.motors_off()
    assert wait(lambda: {"M104 S0", "M140 S0", "M84"} <= set(sent_since(printer, n)))


def test_clear_all(rig):
    printer, engine, server = rig
    engine.filename = "test.gcode"
    n = len(printer.received)
    engine.clear_all()
    assert wait(lambda: {"M104 S0", "M140 S0", "M107", "M84"} <= set(sent_since(printer, n)))
    assert engine.filename is None


def test_lock(rig):
    printer, engine, server = rig
    engine.set_locked(True)
    assert engine.snapshot()["locked"] is True
    engine.set_locked(False)


def test_only_listed_settings_editable(rig):
    printer, engine, server = rig
    before = engine.settings.port
    engine.set_setting("port", "COM99")
    time.sleep(0.5)
    assert engine.settings.port == before


def test_everything_off_is_last(rig):
    """Classic "Off": motors, heaters, fan, power supply."""
    printer, engine, server = rig
    n = len(printer.received)
    engine.everything_off()
    assert wait(lambda: {"M84", "M104 S0", "M140 S0", "M107", "M81"} <= set(sent_since(printer, n)))


def test_two_extruders(tmp_path):
    from layerline.engine import WebHost
    printer = FakePrinter(tools=2)
    events = []
    e = WebHost(events.append)
    e.parse_cmdline([])
    e.start()
    try:
        e.set_setting("extruders", "2")
        e.connect(printer.address, 115200)
        assert wait(lambda: e.p.online)
        assert wait(lambda: e.settings.extruders == 2)
        e.set_tool(1)
        e.set_temp("nozzle", 190)
        assert wait(lambda: "T1" in e.tool_temps and e.tool_temps["T1"][1] == 190, 10)
        n = len(printer.received)
        e.heaters_off()
        assert wait(lambda: {"M104 T0 S0", "M104 T1 S0"} <= set(sent_since(printer, n)))
    finally:
        e.set_setting("extruders", "1")
        time.sleep(0.3)
        e.shutdown()
        printer.stop()
