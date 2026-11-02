"""A simulated Marlin printer (roughly an Ender 5 Pro) for automated testing.

Pronterface can talk to network printers at "host:port", so this listens on a
TCP port and answers like Marlin does over USB: "start" greeting, "ok" after
every command, temperature reports for M105, firmware info for M115, position
for M114. Heaters warm up / cool down gradually so graphs and status look real.

Run it on its own:   python -m pronterface_web.fake_printer 5555   (from the web/ folder)
Then connect to:     127.0.0.1:5555
"""

import re
import socket
import threading
import time

FIRMWARE = ("FIRMWARE_NAME:Marlin 2.0.9.3 (Simulated) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin "
            "PROTOCOL_VERSION:1.0 MACHINE_TYPE:Ender-5 Pro EXTRUDER_COUNT:1")

LINE_NUMBER = re.compile(r"^N\d+\s+")
CHECKSUM = re.compile(r"\*\d+\s*$")


class FakePrinter:
    def __init__(self, port=0, host="127.0.0.1"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(1)
        self.host, self.port = self.sock.getsockname()
        self.lock = threading.Lock()
        self.reset_state()
        self.received = []          # every command received (after stripping N/checksum)
        self._stop = False
        self._conn = None
        threading.Thread(target=self._serve, daemon=True, name="fake-printer").start()
        threading.Thread(target=self._thermals, daemon=True, name="fake-thermals").start()

    @property
    def address(self):
        return "%s:%d" % (self.host, self.port)

    def reset_state(self):
        with self.lock:
            self.nozzle, self.nozzle_target = 24.0, 0.0
            self.bed, self.bed_target = 23.0, 0.0
            self.pos = {"X": 0.0, "Y": 0.0, "Z": 0.0, "E": 0.0}
            self.fan = 0
            self.relative = False
            self.motors_on = False

    # -- simulation ----------------------------------------------------
    def _thermals(self):
        while not self._stop:
            with self.lock:
                for name, rate in (("nozzle", 0.18), ("bed", 0.08)):
                    cur = getattr(self, name)
                    tgt = getattr(self, name + "_target") or 22.0
                    setattr(self, name, cur + (tgt - cur) * rate)
            time.sleep(0.5)

    def temp_report(self):
        with self.lock:
            return "T:%.2f /%.2f B:%.2f /%.2f @:0 B@:0" % (
                self.nozzle, self.nozzle_target, self.bed, self.bed_target)

    # -- networking ----------------------------------------------------
    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self._conn = conn
            self.reset_state()
            try:
                conn.sendall(b"start\necho:Marlin 2.0.9.3\necho: Last Updated: Simulated\n")
                buf = b""
                while not self._stop:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        line = raw.decode("ascii", "replace").strip()
                        if line:
                            conn.sendall(self.handle(line).encode())
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
                self._conn = None

    def handle(self, line):
        line = CHECKSUM.sub("", LINE_NUMBER.sub("", line)).strip()
        self.received.append(line)
        parts = line.split()
        cmd = parts[0].upper()
        args = {}
        for p in parts[1:]:
            try:
                args[p[0].upper()] = float(p[1:]) if len(p) > 1 else None
            except ValueError:
                args[p[0].upper()] = p[1:]
        with self.lock:
            if cmd == "M105":
                pass
            elif cmd in ("M104", "M109") and "S" in args:
                self.nozzle_target = args["S"]
            elif cmd in ("M140", "M190") and "S" in args:
                self.bed_target = args["S"]
            elif cmd == "M106":
                self.fan = int(args.get("S") or 255)
            elif cmd == "M107":
                self.fan = 0
            elif cmd in ("M84", "M18"):
                self.motors_on = False
            elif cmd == "G90":
                self.relative = False
            elif cmd == "G91":
                self.relative = True
            elif cmd == "G28":
                axes = [a for a in "XYZ" if a in args] or list("XYZ")
                for a in axes:
                    self.pos[a] = 0.0
                self.motors_on = True
            elif cmd in ("G0", "G1"):
                for a in "XYZE":
                    if a in args and isinstance(args[a], float):
                        self.pos[a] = self.pos[a] + args[a] if self.relative else args[a]
                self.motors_on = True
            elif cmd == "G92":
                for a in "XYZE":
                    if a in args and isinstance(args[a], float):
                        self.pos[a] = args[a]
        if cmd == "M105":
            return "ok " + self.temp_report() + "\n"
        if cmd == "M115":
            return FIRMWARE + "\nCap:AUTOREPORT_TEMP:1\nok\n"
        if cmd == "M114":
            p = self.pos
            return "X:%.2f Y:%.2f Z:%.2f E:%.2f Count X:0 Y:0 Z:0\nok\n" % (p["X"], p["Y"], p["Z"], p["E"])
        if cmd == "M999":
            return "Resend: 1\nok\n"
        return "ok\n"

    def drop_connection(self):
        """Simulate the USB cable being pulled."""
        if self._conn:
            try:
                self._conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self):
        self._stop = True
        self.drop_connection()
        try:
            self.sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    fp = FakePrinter(int(sys.argv[1]) if len(sys.argv) > 1 else 5555)
    print("Simulated printer listening on", fp.address)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        fp.stop()
