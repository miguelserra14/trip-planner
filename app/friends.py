"""The app of each friend reached through the tunnel (remote.py): the same program, in a process of its own, with its
own reports and maps under reports/people/<friend>/, and a daily limit of what spends the shared quotas.

Why a process each: the app keeps one search, one map, the language and the theme for whoever looks at it (site.Site,
current.SETTINGS), and its reports in one folder (full.REPORTS). A process per friend keeps all of that apart without
changing it, and a friend never sees the owner's reports or another friend's. The owner's app checks who is asking
(server.py) and passes a friend's requests on to that friend's app, which listens on this computer only and is
started the first time the friend asks for a page.

A friend's app may not do what only the owner may (site.OWNER_ONLY), and keeps no spare process (search.keep_spares):
with a few friends that would be a planner loaded in memory each, mostly idle."""
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from trip_planner.trip.trip_settings import MAPS_KEY, REPORTS_KEY, ROOT

PEOPLE = ROOT / "reports" / "people"   # a folder per friend: not a trip_* folder, so the owner's app neither lists nor deletes it
FRIEND_KEY = "TRIP_PLANNER_FRIEND"     # the friend's email, told to their app
START_SECONDS = 60                     # a friend's app loads the planner's modules before it serves: about 10 s
ANSWER_SECONDS = 60                    # a page of a friend's app; a search runs in its own process and never makes a page wait
SERVED = re.compile(r"served at http://127\.0\.0\.1:(\d+)/")   # the line server.serve prints once it listens
# What a friend may start in a day: each search asks Transitous about a hundred times and the coordinator model once
# (about 20 a day for everyone), a map asks Kiwi about a hundred times (150 a day for everyone), a question asks a model
LIMITS = {"search": 3, "map": 2, "question": 10}
# The headers of a friend's app's answer passed on; the length is counted again, the rest are the connection's own
KEPT_HEADERS = ("Location", "Content-Type", "Cache-Control", "Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy")

def folder_of(email: str) -> Path:
    """A friend's folder: readable, and apart from any other email however alike (the hash), never a path from it."""
    readable = re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-")[:40]
    return PEOPLE / f"{readable}-{hashlib.sha256(email.lower().encode()).hexdigest()[:8]}"

def friend() -> str:
    """The friend this app serves, or "" for the owner's."""
    return os.environ.get(FRIEND_KEY, "").strip()

# ---------------------------------------------------------------- the daily limits, inside a friend's app
USES_LOCK = threading.Lock()

def uses_file() -> Path:
    from trip_planner.report import full
    return full.REPORTS / "uses.json"

def allowed(kind: str, today: date | None = None) -> bool:
    """Whether a friend may start one more of a kind today; when so, it is counted. Kept in their folder, so a
    restarted app still knows; the days before today are dropped."""
    day = (today or date.today()).isoformat()
    with USES_LOCK:
        path = uses_file()
        try:
            uses = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            uses = {}
        counted = uses.get(day, {}) if isinstance(uses, dict) else {}
        if counted.get(kind, 0) >= LIMITS[kind]:
            return False
        counted[kind] = counted.get(kind, 0) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({day: counted}, indent=1), encoding="utf-8")
        return True

# ---------------------------------------------------------------- the friends' apps, from the owner's
class Friends:
    """The apps of the friends who asked for a page since the owner's app started, by email."""

    def __init__(self, command: list[str] | None = None):
        self.command = command or [sys.executable, "-m", "app", "--no-browser"]
        self.apps: dict[str, tuple[subprocess.Popen, int]] = {}
        self.lock = threading.Lock()

    def port_of(self, email: str) -> int:
        """The port of a friend's app, started now if it is not running (it ended, or was never asked for)."""
        with self.lock:
            process, port = self.apps.get(email, (None, 0))
            if process is not None and process.poll() is None:
                return port
            folder = folder_of(email)
            environment = {**os.environ, FRIEND_KEY: email, REPORTS_KEY: str(folder / "reports"), MAPS_KEY: str(folder / "maps"),
                           "PYTHONIOENCODING": "utf-8"}
            process = subprocess.Popen(self.command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
            port = listening(process)
            self.apps[email] = (process, port)
            return port

    def forward(self, email: str, method: str, path: str, headers: Any, body: bytes = b"") -> tuple[int, list[tuple[str, str]], bytes]:
        """A friend's request answered by their own app: (status, headers, body). The Host is the app's own address,
        as it only answers that (server.py)."""
        port = self.port_of(email)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=ANSWER_SECONDS)
        try:
            sent = {"Host": f"127.0.0.1:{port}"}
            for name in ("Content-Type", "Content-Length"):
                if headers.get(name):
                    sent[name] = headers[name]
            connection.request(method, path, body=body or None, headers=sent)
            answer = connection.getresponse()
            return answer.status, [(name, value) for name, value in answer.getheaders() if name in KEPT_HEADERS], answer.read()
        finally:
            connection.close()

    def close(self) -> None:
        """The owner's app closing: every friend's app ends with it."""
        with self.lock:
            for process, _ in self.apps.values():
                if process.poll() is None:
                    process.kill()
            self.apps.clear()

def listening(process: subprocess.Popen) -> int:
    """The port a friend's app says it listens on. Its later lines are read on, or its output would fill and stop it."""
    ends = time.monotonic() + START_SECONDS
    found: list[int] = []
    ready = threading.Event()

    def read() -> None:
        for line in process.stdout:
            if not found and (match := SERVED.search(line)):
                found.append(int(match[1]))
                ready.set()
        ready.set()   # it ended without saying so

    threading.Thread(target=read, daemon=True).start()
    if not ready.wait(max(0.0, ends - time.monotonic())) or not found:
        process.kill()
        raise OSError("a friend's app did not start")
    return found[0]
