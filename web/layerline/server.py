"""Small local web server that connects the interface to the engine.

Security: it listens on 127.0.0.1 only (this PC). Because any web page open in
a browser on this PC could still try to reach 127.0.0.1, every launch gets a
random access key; only the app window (which is given the key) can talk to
the engine, and websocket requests from other sites are refused.
"""

import asyncio
import json
import logging
import secrets
import socket
import threading
import time
from pathlib import Path

from aiohttp import web, WSMsgType

from .engine import BAUD_RATES

STATIC = Path(__file__).with_name("static")
COOKIE = "pw_key"
LOG_BACKLOG = 1500


class Server:
    def __init__(self, host="127.0.0.1", port=0):
        self.host = host
        self.port = port
        self.key = secrets.token_urlsafe(24)
        self.engine = None
        self.loop = None
        self.clients = set()
        self.backlog = []           # recent log lines for windows that open later
        self._state_pending = False
        self._ready = threading.Event()
        self.on_quit = None

    # -- called from any thread ------------------------------------------
    def emit(self, event):
        """Engine/log events arrive here from worker threads."""
        if event["type"] == "log":
            self.backlog.append(event)
            if len(self.backlog) > LOG_BACKLOG:
                del self.backlog[:200]
        if event["type"] == "quit" and self.on_quit:
            self.on_quit()
            return
        if self.loop is None:
            return
        if event["type"] == "state":
            # Coalesce bursts of state changes into one update
            if self._state_pending:
                return
            self._state_pending = True
            self.loop.call_soon_threadsafe(lambda: self.loop.call_later(0.05, self._send_state))
            return
        self.loop.call_soon_threadsafe(self._broadcast, event)

    # -- event loop side --------------------------------------------------
    def _send_state(self):
        self._state_pending = False
        self._broadcast(self._state_event())

    def _state_event(self):
        state = self.engine.snapshot()
        return {"type": "state", "state": state}

    def _broadcast(self, event):
        data = json.dumps(event)
        for ws in list(self.clients):
            if not ws.closed:
                asyncio.ensure_future(ws.send_str(data))

    async def _ticker(self):
        """Push status once a second (temps etc. change without events)."""
        while True:
            await asyncio.sleep(1)
            if self.clients:
                self._broadcast(self._state_event())

    # -- access control ---------------------------------------------------
    def _authorized(self, request):
        return secrets.compare_digest(request.cookies.get(COOKIE, ""), self.key) or \
            secrets.compare_digest(request.query.get("key", ""), self.key)

    def _same_origin(self, request):
        origin = request.headers.get("Origin")
        if origin is None:
            return True
        return origin in ("http://%s:%d" % (self.host, self.port),
                          "http://localhost:%d" % self.port)

    @web.middleware
    async def _guard(self, request, handler):
        if not self._authorized(request):
            return web.Response(status=403, text="Open Layerline from its app window.")
        response = await handler(request)
        if request.query.get("key") == self.key:
            response.set_cookie(COOKIE, self.key, httponly=True, samesite="Strict")
        return response

    # -- routes -----------------------------------------------------------
    async def index(self, request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def initial(self, request):
        e = self.engine
        return web.json_response({
            "state": e.snapshot(),
            "ports": e.scanserial(),
            "bauds": BAUD_RATES,
            "history": e.read_history(),
            "log": self.backlog[-LOG_BACKLOG:],
            "buttons": e.custombuttons,
            "temp_history": list(e.temp_history),
            "settings_info": e.settings_info(),
        })

    async def websocket(self, request):
        if not self._same_origin(request):
            return web.Response(status=403, text="Wrong origin")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_str(json.dumps(self._state_event()))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle(ws, json.loads(msg.data))
        finally:
            self.clients.discard(ws)
        return ws

    async def _handle(self, ws, msg):
        e = self.engine
        action = msg.get("action")
        if action == "command":
            line = str(msg.get("line", "")).strip()
            if line:
                logging.info("> " + line)
                e.add_history(line)
                e.submit(line)
        elif action == "connect":
            e.connect(msg.get("port"), msg.get("baud") or 115200)
        elif action == "disconnect":
            e.disconnect()
        elif action == "reset":
            e.reset()
        elif action == "rescan":
            ports = await asyncio.get_running_loop().run_in_executor(None, e.scanserial)
            await ws.send_str(json.dumps({"type": "ports", "ports": ports}))
        elif action == "debug":
            e.set_debug(msg.get("on"))
        elif action == "setting":
            e.set_setting(str(msg.get("name")), str(msg.get("value")))
        elif action == "settemp":
            e.set_temp(msg.get("heater"), msg.get("value"), bool(msg.get("confirmed")))
        elif action == "preheat":
            e.preheat(str(msg.get("material")))
        elif action == "heaters_off":
            e.heaters_off()
        elif action == "motors_off":
            e.motors_off()
        elif action == "everything_off":
            e.everything_off()
        elif action == "clear_all":
            e.clear_all()
        elif action == "tool":
            e.set_tool(msg.get("tool", 0))
        elif action == "lock":
            e.set_locked(msg.get("on"))
        elif action == "clearlog":
            self.backlog.clear()
        elif action == "quit":
            if self.on_quit:
                self.on_quit()
            return
        self.emit({"type": "state"})

    # -- startup ----------------------------------------------------------
    def _make_app(self):
        app = web.Application(middlewares=[self._guard])
        app.router.add_get("/", self.index)
        app.router.add_get("/api/initial", self.initial)
        app.router.add_get("/ws", self.websocket)
        app.router.add_static("/static/", STATIC)
        return app

    def _pick_port(self):
        if self.port:
            with socket.socket() as s:
                try:
                    s.bind((self.host, self.port))
                    return self.port
                except OSError:
                    logging.warning("Port %d is busy, picking another." % self.port)
        with socket.socket() as s:
            s.bind((self.host, 0))
            return s.getsockname()[1]

    def start(self, engine):
        """Start serving on a background thread. Returns the URL to open."""
        self.engine = engine
        self.port = self._pick_port()
        threading.Thread(target=self._serve, name="web server", daemon=True).start()
        if not self._ready.wait(15):
            raise RuntimeError("Web server didn't start")
        return "http://%s:%d/?key=%s" % (self.host, self.port, self.key)

    def _serve(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        runner = web.AppRunner(self._make_app(), access_log=None)
        self.loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, self.host, self.port)
        self.loop.run_until_complete(site.start())
        self.loop.create_task(self._ticker())
        self._ready.set()
        self.loop.run_forever()
