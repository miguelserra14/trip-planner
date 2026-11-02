"""The app's pages, served on this computer only. The same rules as the map's server (discover/serve.py), because
any web page the traveller has open could send a request to this computer:
- it listens on 127.0.0.1 and answers only requests whose Host is that address (a name that points there, DNS
  rebinding, is refused);
- it only reads for now: GET, and what is served is looked up by its key among what exists, never a path built
  from the request;
- the pages run no script and load nothing from elsewhere, and the browser is told so (Content-Security-Policy),
  so that a text a model wrote in a report cannot become one."""
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

HOST = "127.0.0.1"
# What the pages may do: nothing but their own inline style. Links still open, in a new tab
POLICY = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

# What a page is made from its path: (HTTP status, the page's HTML)
Answer = Callable[[str], tuple[int, str]]

def start(answer: Answer) -> ThreadingHTTPServer:
    """Serve the pages in the background, until stop(). The address is address_of(server)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host") != f"{HOST}:{self.server.server_address[1]}":
                status, text = 404, "not found"
            else:
                status, text = answer(self.path)
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", POLICY)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *arguments) -> None:
            pass   # the terminal is for the app's own lines

    server = ThreadingHTTPServer((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def address_of(server: ThreadingHTTPServer) -> str:
    return f"http://{HOST}:{server.server_address[1]}/"

def stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()

def serve(answer: Answer, browser: bool = True) -> None:
    """Serve the pages until Ctrl+C, for the terminal."""
    server = start(answer)
    address = address_of(server)
    print(f"The trip planner's pages are served at {address}. Ctrl+C to stop.", flush=True)
    if browser:
        webbrowser.open(address)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)
