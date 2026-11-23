"""A few friends reach the app on this computer through a Cloudflare Tunnel, behind Cloudflare Access (a login by
email, a one-time PIN). Off unless every key below is set in env/.env; then the app listens on REMOTE_PORT, where
cloudflared sends what reaches the public hostname.

A request is answered when it is either:
- the owner's, on this computer: its Host is 127.0.0.1 and the app's port (as before, see server.py);
- a friend's: its Host is exactly REMOTE_HOST and it carries a Cf-Access-Jwt-Assertion that Cloudflare signed for
  this application (its audience tag) less than its lifetime ago. The email inside names the friend.
Anything else is refused. The assertion is checked here and not only trusted because the tunnel is not the only way
to reach the port: any program on this computer could send the header."""
import dataclasses
import os
from typing import Any, Callable

import jwt

HOST_KEY = "REMOTE_HOST"        # the public hostname the tunnel serves, e.g. trips.example.com
PORT_KEY = "REMOTE_PORT"        # the port cloudflared sends to, on 127.0.0.1
TEAM_KEY = "ACCESS_TEAM"        # the Zero Trust team name: <team>.cloudflareaccess.com
AUDIENCE_KEY = "ACCESS_AUD"     # the Access application's audience tag (its Overview page)
KEYS = (HOST_KEY, PORT_KEY, TEAM_KEY, AUDIENCE_KEY)
ASSERTION = "Cf-Access-Jwt-Assertion"   # the header Access adds to every request it lets through
KEYS_SECONDS = 3600             # Cloudflare's signing keys are asked again after this long: they rotate every few weeks
LEEWAY_SECONDS = 30             # this computer's clock and Cloudflare's may differ this much
OWNER = ""                      # the viewer on this computer: every page and every task

@dataclasses.dataclass(frozen=True)
class Remote:
    host: str
    port: int
    team: str
    audience: str
    # The public key a token was signed with, by its header: Cloudflare's published keys, or a test's own
    signing_key: Callable[[str], Any] | None = None

    @property
    def issuer(self) -> str:
        return f"https://{self.team}.cloudflareaccess.com"

    def key_of(self, token: str) -> Any:
        if self.signing_key is not None:
            return self.signing_key(token)
        return keys_of(self.issuer).get_signing_key_from_jwt(token).key

    def email_of(self, token: str) -> str | None:
        """The friend's email, when the token is Cloudflare's for this application and still valid; else None."""
        if not token:
            return None
        try:
            claims = jwt.decode(token, self.key_of(token), algorithms=["RS256"], audience=self.audience,
                                issuer=self.issuer, leeway=LEEWAY_SECONDS, options={"require": ["exp", "aud", "iss"]})
        except (jwt.PyJWTError, ValueError):
            return None
        email = str(claims.get("email") or "").strip().lower()
        return email or None

CLIENTS: dict[str, jwt.PyJWKClient] = {}   # one client per team, which keeps the keys it read for KEYS_SECONDS

def keys_of(issuer: str) -> jwt.PyJWKClient:
    if issuer not in CLIENTS:
        CLIENTS[issuer] = jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_keys=True, lifespan=KEYS_SECONDS, timeout=10)
    return CLIENTS[issuer]

def configured(environ: dict[str, str] | None = None) -> Remote | None:
    """The remote access env/.env asks for, or None when none of its keys is set. Some set and others not is a
    mistake said at once, rather than an app that half listens."""
    environ = os.environ if environ is None else environ
    values = {key: environ.get(key, "").strip() for key in KEYS}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError(f"Remote access needs {', '.join(KEYS)} in env/.env; missing: {', '.join(missing)}.")
    if not values[PORT_KEY].isdigit() or not 1024 <= int(values[PORT_KEY]) <= 65535:
        raise ValueError(f"{PORT_KEY} must be a port number from 1024 to 65535, not {values[PORT_KEY]!r}.")
    host = values[HOST_KEY].lower()
    if "/" in host or ":" in host:
        raise ValueError(f"{HOST_KEY} is a hostname only, such as trips.example.com, not {values[HOST_KEY]!r}.")
    return Remote(host, int(values[PORT_KEY]), values[TEAM_KEY], values[AUDIENCE_KEY])

def viewer_of(remote: Remote | None, local_host: str, host: str | None, assertion: str | None) -> str | None:
    """Who sent a request: OWNER on this computer, a friend's email through the tunnel, None when refused."""
    if host == local_host:
        return OWNER
    if remote is not None and (host or "").lower() == remote.host:
        return remote.email_of(assertion or "")
    return None
