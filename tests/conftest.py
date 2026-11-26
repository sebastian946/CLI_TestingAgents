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
SESSION_COOKIE = "session=abc123"
ACCOUNT_PAGE = "<title>Account</title><h1>Welcome back</h1>"
LOGIN_PAGE = """
<title>Sign in</title>
<h1>Sign in</h1>
<label>Password <input type="password"></label>
<button onclick="
  document.cookie = 'session=abc123; path=/';
  localStorage.setItem('token', 't-1');
  document.title = 'Signed in';
">Log in</button>
"""
# Pages anyone can open.
PUBLIC_PAGES = {
    "/landing": "<title>Landing</title><h1>Landing</h1>",
    "/login": LOGIN_PAGE,
    # A login screen whose URL does not say so.
    "/welcome": LOGIN_PAGE,
    # A login step without a password field (it only asks for the email).
    "/auth/start": "<title>Email</title><label>Email <input type='email'></label>",
    # The password field exists but the user cannot see it.
    "/hidden-password": "<title>Promo</title><input type='password' hidden>",
}
# Where each route sends every visitor, with or without a session.
REDIRECTS = {"/redirect": "/landing", "/moved": "/sample_page.html"}
# Private routes: where each one sends a visitor who has no session.
PRIVATE_REDIRECTS = {
    "/account": "/login",
    "/dashboard": "/welcome",
    "/orders": "/auth/start",
    "/promo": "/hidden-password",
}
# A single-page app: the server always answers 200 and JavaScript redirects.
SPA_PAGE = """
<title>App</title><h1>App</h1>
<script>
  if (!document.cookie.includes('session=abc123')) location.replace('/welcome');
</script>
"""


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serves tests/fixtures plus a few routes that simulate server behavior."""

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        logged_in = SESSION_COOKIE in cookie
        if self.path in REDIRECTS:
            self._redirect(REDIRECTS[self.path])
        elif self.path in PUBLIC_PAGES:
            self._send_html(PUBLIC_PAGES[self.path])
        elif self.path in PRIVATE_REDIRECTS:
            if logged_in:
                self._send_html(ACCOUNT_PAGE)
            else:
                self._redirect(PRIVATE_REDIRECTS[self.path])
        elif self.path == "/private":
            # A private route that answers 401 instead of redirecting.
            if logged_in:
                self._send_html(ACCOUNT_PAGE)
            else:
                self.send_error(401)
        elif self.path == "/app":
            self._send_html(SPA_PAGE)
        elif self.path == "/error":
            self.send_error(500)
        elif self.path == "/slow":
            time.sleep(SLOW_SECONDS)
            self._send_html("<title>Slow</title><h1>Slow</h1>")
        elif self.path == "/whoami":
            self._send_html(f"<title>Who</title><h1>{cookie or 'no cookie'}</h1>")
        else:
            super().do_GET()

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

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
