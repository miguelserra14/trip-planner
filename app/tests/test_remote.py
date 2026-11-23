"""Remote access through a Cloudflare Tunnel (remote.py, server.py): the keys in env/.env, the assertion Cloudflare
Access signs, the Host the tunnel sends, and what only the owner may do. Tokens are signed here with a key made for
the test: no network but this computer's own address."""
import http.client
import sys
import time
from datetime import date

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import friends, remote, server
from app.site import Site
from app.tests.test_reports import Answers
from settings import SETTINGS
from trip_planner.texts import tr

HOST = "trips.example.com"
TEAM = "friends"
AUDIENCE = "a1b2c3"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

def access(port: int = 0) -> remote.Remote:
    return remote.Remote(HOST, port, TEAM, AUDIENCE, signing_key=lambda token: KEY.public_key())

def signed(key=KEY, **changed) -> str:
    claims = {"email": "Ana@Example.com", "aud": [AUDIENCE], "iss": f"https://{TEAM}.cloudflareaccess.com",
              "iat": int(time.time()), "exp": int(time.time()) + 600, **changed}
    return jwt.encode(claims, key, algorithm="RS256")

def ask(port: int, method: str, host: str, token: str | None = None, body: str = "") -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Host": host, **({remote.ASSERTION: token} if token else {})}
        if method == "POST":
            headers["Content-Length"] = str(len(body))
        connection.request(method, "/", body=body or None, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()

def test_remote_off_by_default():
    assert remote.configured({}) is None

def test_remote_half_set_refused():
    with pytest.raises(ValueError, match=remote.AUDIENCE_KEY):
        remote.configured({remote.HOST_KEY: HOST, remote.PORT_KEY: "8765", remote.TEAM_KEY: TEAM})
    with pytest.raises(ValueError, match=remote.PORT_KEY):
        remote.configured({remote.HOST_KEY: HOST, remote.PORT_KEY: "80", remote.TEAM_KEY: TEAM, remote.AUDIENCE_KEY: AUDIENCE})
    with pytest.raises(ValueError, match=remote.HOST_KEY):
        remote.configured({remote.HOST_KEY: "https://trips.example.com", remote.PORT_KEY: "8765", remote.TEAM_KEY: TEAM,
                           remote.AUDIENCE_KEY: AUDIENCE})

def test_assertion_names_friend():
    assert access().email_of(signed()) == "ana@example.com"

@pytest.mark.parametrize("token", [signed(OTHER_KEY), signed(aud=["another app"]), signed(iss="https://other.cloudflareaccess.com"),
                                   signed(exp=int(time.time()) - 600), signed(email=""), "not a token", ""])
def test_bad_assertion_refused(token):
    assert access().email_of(token) is None

def test_unsigned_token_refused():
    token = jwt.encode({"email": "ana@example.com", "aud": AUDIENCE, "iss": access().issuer, "exp": int(time.time()) + 600},
                       key=None, algorithm="none")
    assert access().email_of(token) is None

class FriendApps:
    """Stands in for the friends' apps (friends.Friends): says whose app answered."""
    def __init__(self):
        self.asked = []

    def forward(self, email, method, path, headers, body=b""):
        self.asked.append((email, method, path, body))
        return 200, [("Content-Type", "text/plain; charset=utf-8")], f"the app of {email}".encode()

def test_server_tells_viewers_apart():
    apps = FriendApps()
    running = server.start(Answers(), access(), apps)
    try:
        port = running.server_address[1]
        assert ask(port, "GET", f"127.0.0.1:{port}") == (200, "the page for the owner")
        assert ask(port, "GET", HOST, signed()) == (200, "the app of ana@example.com")
        assert ask(port, "POST", HOST.upper(), signed(), body="token=x") == (200, "the app of ana@example.com")
        assert apps.asked[-1] == ("ana@example.com", "POST", "/", b"token=x")
        assert ask(port, "GET", HOST)[0] == 404                       # no assertion: not through Access
        assert ask(port, "GET", "other.example.com", signed())[0] == 404
        assert ask(port, "GET", f"127.0.0.1:{port}", signed(OTHER_KEY))[0] == 200   # the owner needs none
        assert len(apps.asked) == 2
    finally:
        server.stop(running)

def test_tunnel_off_host_refused():
    running = server.start(Answers())
    try:
        assert ask(running.server_address[1], "GET", HOST, signed())[0] == 404
    finally:
        server.stop(running)

@pytest.mark.parametrize("path", ["/quit", "/language/add", "/prices", "/check"])
def test_owner_only_for_friends(path):
    site = Site(SETTINGS, start=lambda first: None, stale=list)
    reply = site.post(path, f"token={site.token}".encode(), viewer="ana@example.com")
    assert reply.status == 403 and tr("app_owner_only") in reply.html and not site.quitting.is_set()

def test_friend_app_refuses_quit():
    site = Site(SETTINGS, start=lambda first: None, stale=list, friend="ana@example.com")
    reply = site.post("/quit", f"token={site.token}".encode())   # from this computer, as the owner's app passes it on
    assert reply.status == 403 and not site.quitting.is_set()

def test_daily_limit_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(friends, "uses_file", lambda: tmp_path / "uses.json")
    monday, tuesday = date(2026, 12, 7), date(2026, 12, 8)
    assert [friends.allowed("search", monday) for _ in range(friends.LIMITS["search"] + 1)][-2:] == [True, False]
    assert friends.allowed("map", monday) and friends.allowed("search", tuesday)

def test_friend_search_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(friends, "uses_file", lambda: tmp_path / "uses.json")
    monkeypatch.setattr(friends, "LIMITS", {**friends.LIMITS, "search": 0})
    started = []
    site = Site(SETTINGS, start=lambda first: started.append(first), stale=list, friend="ana@example.com")
    body = f"token={site.token}&origin=Coimbra&destination=Bruxelas&dates=07/12/2026".encode()
    reply = site.post("/search", body)
    assert reply.status == 429 and not started

def test_friend_folders_apart():
    first, second = friends.folder_of("Ana@Example.com"), friends.folder_of("ana@example.co")
    assert first == friends.folder_of("ana@example.com") and first != second
    assert first.parent == friends.PEOPLE and first.name.startswith("ana-example-com-")
    assert friends.folder_of("../../etc").parent == friends.PEOPLE

APP = """import os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (os.environ["TRIP_PLANNER_FRIEND"] + " " + os.environ["TRIP_PLANNER_REPORTS"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Set-Cookie", "dropped=1")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *arguments):
        pass
server = HTTPServer(("127.0.0.1", 0), Handler)
print("The trip planner is served at http://127.0.0.1:%d/. Ctrl+C to stop." % server.server_address[1], flush=True)
server.serve_forever()
"""

def test_friend_app_started_once(tmp_path):
    script = tmp_path / "app.py"
    script.write_text(APP, encoding="utf-8")
    apps = friends.Friends([sys.executable, str(script)])
    try:
        status, headers, body = apps.forward("ana@example.com", "GET", "/", {})
        assert status == 200 and body.decode().startswith("ana@example.com ")
        assert body.decode().endswith(str(friends.folder_of("ana@example.com") / "reports"))
        assert "Set-Cookie" not in dict(headers)
        port = apps.port_of("ana@example.com")
        assert apps.port_of("ana@example.com") == port and apps.port_of("rui@example.com") != port
    finally:
        apps.close()
