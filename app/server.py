"""The app's pages, served on this computer only. Any web page the traveller has open could send a request to this
computer, so:
- it listens on 127.0.0.1 and answers only requests whose Host is that address (a name that points there, DNS
  rebinding, is refused);
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

def start(site: Site) -> ThreadingHTTPServer:
    """Serve the site in the background, until stop(). The address is address_of(server)."""

    class Handler(BaseHTTPRequestHandler):
        def local(self) -> bool:
            return self.headers.get("Host") == f"{HOST}:{self.server.server_address[1]}"

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

        def do_GET(self) -> None:
            self.reply(site.get(self.path) if self.local() else Reply(404, "not found"))

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
            self.reply(site.post(self.path, body) if self.local() else Reply(404, "not found"))

        def log_message(self, *arguments) -> None:
            pass   # the terminal is for the app's own lines

    server = Server((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def address_of(server: ThreadingHTTPServer) -> str:
    return f"http://{HOST}:{server.server_address[1]}/"

def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()

def serve(site: Site, browser: bool = True) -> None:
    """Serve the site until the Quit button or Ctrl+C. A search or a map still running is stopped with it."""
    server = start(site)
    address = address_of(server)
    print(f"The trip planner is served at {address}. Ctrl+C to stop.", flush=True)
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
        site.close()
