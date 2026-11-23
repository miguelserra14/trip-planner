"""The app's pages, served on this computer only. Any web page the traveller has open could send a request to this
computer, so:
- it listens on 127.0.0.1 and answers only requests whose Host is that address (a name that points there, DNS
  rebinding, is refused), or, when remote access is set (remote.py), the public hostname with Cloudflare Access's
  signed assertion;
- a POST, which starts or stops a search, must carry the token of the app's own forms (site.py), and is small;
- what is served is looked up by its key among what exists, never a path built from the request;
- the pages run no script but the app's own file (page.js), load nothing from elsewhere and send their forms and
  requests only here, and the browser is told so (Content-Security-Policy), so that a text a model wrote in a
  report cannot become a script."""
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.friends import Friends
from app.remote import ASSERTION, OWNER, Remote, viewer_of
from app.site import Reply, Site

HOST = "127.0.0.1"
MAX_BODY_BYTES = 16000  # the form of a trip with every advanced setting, a few wishes in words included
# What the pages may do: the app's own style (page.css, and inline for what is drawn to scale), the app's script, and
# forms and requests sent back here.
# Links still open, in a new tab
POLICY = ("default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; "
          "form-action 'self'; frame-ancestors 'none'")

class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        """A tab closed, or a page left while it was still asking: the browser dropped the connection, nothing went
        wrong here. Anything else is printed, as by default."""
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)

def start(site: Site, remote: Remote | None = None, friends: Friends | None = None) -> ThreadingHTTPServer:
    """Serve the site in the background, until stop(). The address is address_of(server). With remote access the
    port is the one cloudflared sends to, otherwise any free one, and a friend's requests are answered by their own
    app (friends.py)."""

    class Handler(BaseHTTPRequestHandler):
        def viewer(self) -> str | None:
            return viewer_of(remote, f"{HOST}:{self.server.server_address[1]}", self.headers.get("Host"), self.headers.get(ASSERTION))

        def reply(self, reply: Reply) -> None:
            body = reply.html.encode("utf-8")
            self.send_response(reply.status)
            if reply.location:
                self.send_header("Location", reply.location)
            self.send_header("Content-Type", reply.kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", reply.policy or POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", reply.referrer)
            self.end_headers()
            self.wfile.write(body)

        def passed_on(self, email: str, body: bytes = b"") -> None:
            """A friend's request answered by their own app, as it answered."""
            if friends is None:
                return self.reply(Reply(404, "not found"))
            try:
                status, headers, answer = friends.forward(email, self.command, self.path, self.headers, body)
            except OSError:
                return self.reply(Reply(503, "The app is starting or busy: try again in a minute."))
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def do_GET(self) -> None:
            viewer = self.viewer()
            if viewer is None:
                return self.reply(Reply(404, "not found"))
            if viewer != OWNER:
                return self.passed_on(viewer)
            self.reply(site.get(self.path, viewer))

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY_BYTES:
                self.close_connection = True
                return self.reply(Reply(400, "not a form of this app"))
            # Read even when it is refused: Windows resets a connection closed with data unread, and the answer is lost
            body = self.rfile.read(length)
            viewer = self.viewer()
            if viewer is None:
                return self.reply(Reply(404, "not found"))
            if viewer != OWNER:
                return self.passed_on(viewer, body)
            self.reply(site.post(self.path, body, viewer))

        def log_message(self, *arguments) -> None:
            pass   # the terminal is for the app's own lines

    server = Server((HOST, remote.port if remote else 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def address_of(server: ThreadingHTTPServer) -> str:
    return f"http://{HOST}:{server.server_address[1]}/"

def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()

def serve(site: Site, browser: bool = True, remote: Remote | None = None, friends: Friends | None = None) -> None:
    """Serve the site until the Quit button or Ctrl+C. A search or a map still running is stopped with it, and so
    is every friend's app."""
    server = start(site, remote, friends)
    address = address_of(server)
    print(f"The trip planner is served at {address}. Ctrl+C to stop.", flush=True)
    if remote:
        print(f"Friends reach it at https://{remote.host}/ through the tunnel, behind Cloudflare Access.", flush=True)
    if browser:
        webbrowser.open(address)
    try:
        while not site.quitting.wait(1):   # until the Quit button, or Ctrl+C (or its terminal window closed)
            pass
        time.sleep(0.5)   # the page that says so reaches the browser first
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)
        if friends is not None:
            friends.close()
        site.close()
