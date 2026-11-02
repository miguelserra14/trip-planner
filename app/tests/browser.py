"""A headless Edge or Chrome for the tests of the page (test_page.py), driven over its DevTools protocol with the
websockets package the planner already has: open a page, run a line of script in it, wait for something to be true."""
import json
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

# Where a browser usually is on this computer, the first one found is used
BROWSERS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]

def found() -> Path | None:
    return next((path for path in BROWSERS if path.exists()), None)

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

class Tab:
    """One tab of the browser."""

    def __init__(self, url: str):
        self.socket = connect(url, max_size=None)
        self.counter = 0
        self.call("Page.enable")

    def call(self, method: str, **params):
        self.counter += 1
        self.socket.send(json.dumps({"id": self.counter, "method": method, "params": params}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("id") == self.counter:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    def js(self, expression: str):
        """The value of a line of script in the page. A page element comes back as {}: ask for !!it."""
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise AssertionError(result["exceptionDetails"].get("exception", {}).get("description", result["exceptionDetails"]))
        return result["result"].get("value")

    def wait(self, expression: str, seconds: float = 10) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self.js(expression):
                return
            time.sleep(0.1)
        raise AssertionError(f"never true: {expression}")

    def open(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        self.loaded()

    def loaded(self) -> None:
        """Wait for the page to be there, and mark it: the mark is gone if the page is loaded again."""
        time.sleep(0.3)
        self.wait("document.readyState === 'complete' && !!document.body")
        self.js("window.sameLoad = true")

    def submit(self, selector: str) -> None:
        self.js(f"document.querySelector({json.dumps(selector)}).submit()")
        self.loaded()

class Browser:
    def __init__(self, path: Path):
        self.port = free_port()
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # the browser may still hold a file
        self.process = subprocess.Popen([str(path), "--headless=new", f"--remote-debugging-port={self.port}",
                                         f"--user-data-dir={self.folder.name}", "--no-first-run", "--window-size=1100,900",
                                         "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.targets()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("the browser did not start")

    def targets(self) -> list[dict]:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json"))

    def tab(self) -> Tab:
        """A new tab."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        return Tab(json.load(urllib.request.urlopen(request))["webSocketDebuggerUrl"])

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        self.folder.cleanup()
