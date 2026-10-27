"""The map, served on this computer only, so that a click on a trip can open the planner's window on it (plan.py).
A map opened as a file works too, without that button: it shows the command to run instead.

Any web page the traveller has open could send a request to this computer, so the server answers only:
- on 127.0.0.1, to requests whose Host is that address (a name that points there, DNS rebinding, is refused);
- to a plan request carrying the page's token, a random one made for each map, that no other page can read;
- with a trip of the map, by its key: the command is built from the map's own trips, never from what was sent.
While the timetables are looked up (confirm.py) the page asks /state for the destinations again: nothing in it is
secret, and another page cannot read it anyway, the browser keeps it to the page's own address."""
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from discover.plan import command
from trip_planner.texts import tr

ROOT = Path(__file__).parents[1]   # the planner is run from the repository's folder
HOST = "127.0.0.1"
MAX_BODY_BYTES = 2000      # a plan request is a key and a token
REPEAT_SECONDS = 10        # the same trip asked again this soon is a double click, not a second window

def new_token() -> str:
    return secrets.token_urlsafe(24)

class Planner:
    """Which trips may be opened in the planner, and the last time each one was."""

    def __init__(self, trips: dict[str, list[str]], token: str, start: Callable[[list[str]], None]):
        self.trips, self.token, self.start = trips, token, start
        self.opened: dict[str, float] = {}

    def ask(self, body: bytes, now: float) -> tuple[int, str]:
        """The answer to a plan request: (HTTP status, what to tell the page)."""
        try:
            asked = json.loads(body)
            token, key = asked["token"], asked["trip"]
        except (ValueError, KeyError, TypeError):
            return 400, tr("discover_serve_not_plan")
        if not isinstance(token, str) or not secrets.compare_digest(token, self.token):
            return 403, tr("discover_serve_not_this_map")
        if not isinstance(key, str) or key not in self.trips:
            return 404, tr("discover_serve_not_a_trip")
        if now - self.opened.get(key, -REPEAT_SECONDS) < REPEAT_SECONDS:
            return 200, tr("discover_serve_already_opening")
        self.opened[key] = now
        self.start(command(self.trips[key]))
        return 200, tr("discover_serve_opening")

def open_window(arguments: list[str]) -> None:
    """The planner in its own process, in its own group: Ctrl+C on the map's terminal does not close it."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen(arguments, cwd=ROOT, creationflags=flags)

def start(page: str, planner: Planner, state: Callable[[], dict] = dict) -> ThreadingHTTPServer:
    """Serve the page in the background, until the server's shutdown(). state gives what the page shows now,
    asked again while it changes. The address is address_of(server)."""
    content = page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def local(self) -> bool:
            return self.headers.get("Host") == f"{HOST}:{self.server.server_address[1]}"

        def answer(self, status: int, kind: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self.local() or self.path not in ("/", "/index.html", "/state"):
                return self.answer(404, "text/plain; charset=utf-8", b"not found")
            if self.path == "/state":
                return self.answer(200, "application/json", json.dumps(state(), ensure_ascii=False).encode("utf-8"))
            self.answer(200, "text/html; charset=utf-8", content)

        def do_POST(self) -> None:
            if not self.local() or self.path != "/plan":
                return self.answer(404, "text/plain; charset=utf-8", b"not found")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY_BYTES:
                return self.answer(400, "text/plain; charset=utf-8", b"not a plan request")
            status, text = planner.ask(self.rfile.read(length), time.monotonic())
            self.answer(status, "application/json", json.dumps({"result": text}).encode("utf-8"))

        def log_message(self, *arguments) -> None:
            pass   # the terminal is for the map's own lines

    server = ThreadingHTTPServer((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def address_of(server: ThreadingHTTPServer) -> str:
    return f"http://{HOST}:{server.server_address[1]}/"

def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()

def serve(page: str, planner: Planner, state: Callable[[], dict] = dict, browser: bool = True) -> None:
    """Serve the page until Ctrl+C, for the terminal."""
    server = start(page, planner, state)
    address = address_of(server)
    print(f"The map is served at {address}: a click on a trip there opens the planner on it. Ctrl+C to stop.", flush=True)
    if browser:
        webbrowser.open(address)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)
