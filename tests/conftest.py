import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

CONFIG_VARS = (
    "TESTWISE_MODEL",
    "TESTWISE_CHEAP_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Empty project directory, with no testwise configuration in the environment."""
    saved = dict(os.environ)
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # get_settings() exports the .env to os.environ; restore it to isolate tests.
    os.environ.clear()
    os.environ.update(saved)


FIXTURES = Path(__file__).parent / "fixtures"
SLOW_SECONDS = 3
LOGIN_PAGE = """
<title>Sign in</title>
<h1>Sign in</h1>
<button onclick="
  document.cookie = 'session=abc123; path=/';
  localStorage.setItem('token', 't-1');
  document.title = 'Signed in';
">Log in</button>
"""


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serves tests/fixtures plus a few routes that simulate server behavior."""

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/sample_page.html")
            self.end_headers()
        elif self.path == "/error":
            self.send_error(500)
        elif self.path == "/slow":
            time.sleep(SLOW_SECONDS)
            self._send_html("<title>Slow</title><h1>Slow</h1>")
        elif self.path == "/whoami":
            self._send_html(f"<title>Who</title><h1>{cookie or 'no cookie'}</h1>")
        elif self.path == "/login":
            self._send_html(LOGIN_PAGE)
        elif self.path == "/private":
            # Only reachable with the cookie that the login page sets.
            if "session=abc123" in cookie:
                self._send_html("<title>Account</title><h1>Welcome back</h1>")
            else:
                self.send_error(401)
        else:
            super().do_GET()

    def _send_html(self, body: str) -> None:
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture(scope="session")
def site():
    """Local HTTP server with the HTML fixtures. Yields its base URL."""
    handler = partial(FixtureHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
