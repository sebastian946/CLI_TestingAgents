import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testwise.cli import app
from testwise.explorer import ExploreError, PageSnapshot, explore
from testwise.explorer.explorer import _describe_error

FIXTURES = Path(__file__).parent / "fixtures"
SLOW_SECONDS = 3
runner = CliRunner()


class FixtureHandler(SimpleHTTPRequestHandler):
    """Serves tests/fixtures plus a few routes that simulate server behavior."""

    def do_GET(self):
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
            cookie = self.headers.get("Cookie", "no cookie")
            self._send_html(f"<title>Who</title><h1>{cookie}</h1>")
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


@pytest.fixture(scope="module")
def site():
    """Local HTTP server with the HTML fixtures. Yields its base URL."""
    handler = partial(FixtureHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


def test_explore_returns_a_page_snapshot(site):
    before = datetime.now(UTC)

    snapshot = explore(f"{site}/sample_page.html")

    assert isinstance(snapshot, PageSnapshot)
    assert snapshot.url == f"{site}/sample_page.html"
    assert snapshot.title == "Sample Shop"
    assert before <= snapshot.captured_at <= datetime.now(UTC) + timedelta(seconds=1)


def test_aria_snapshot_has_roles_and_names(site):
    aria = explore(f"{site}/sample_page.html").aria_snapshot

    assert 'heading "Sample Shop" [level=1]' in aria
    assert 'textbox "Username"' in aria
    assert 'button "Log in"' in aria
    assert 'link "About us"' in aria


def test_content_rendered_after_load_is_captured(site):
    assert "Backpack" in explore(f"{site}/sample_page.html").aria_snapshot


def test_url_is_the_final_one_after_redirects(site):
    assert explore(f"{site}/redirect").url == f"{site}/sample_page.html"


def test_storage_state_is_applied(site):
    host = "127.0.0.1"
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "abc123",
                "domain": host,
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }

    assert "session=abc123" in explore(f"{site}/whoami", state).aria_snapshot


def test_storage_state_can_be_a_file(site, tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"cookies": [], "origins": []}))

    assert "no cookie" in explore(f"{site}/whoami", state_file).aria_snapshot


def test_missing_storage_state_file_is_reported(site, tmp_path):
    with pytest.raises(ExploreError, match="Storage state file not found"):
        explore(f"{site}/sample_page.html", tmp_path / "missing.json")


@pytest.mark.parametrize(("path", "status"), [("/missing.html", 404), ("/error", 500)])
def test_http_errors_are_reported(site, path, status):
    with pytest.raises(ExploreError, match=f"HTTP {status}"):
        explore(f"{site}{path}")


def test_timeout_is_reported(site):
    with pytest.raises(ExploreError, match="timed out after 0.5 seconds"):
        explore(f"{site}/slow", timeout=0.5)


def test_connection_refused_is_reported():
    with pytest.raises(ExploreError, match="connection refused"):
        explore(closed_port_url())


@pytest.mark.parametrize("url", ["example.com", "ftp://example.com", ""])
def test_url_without_a_supported_scheme_is_rejected(url):
    with pytest.raises(ExploreError, match="Invalid URL"):
        explore(url)


def test_unknown_domain_message():
    message = (
        "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://nope.invalid/\nCall log:"
    )

    assert "domain not found" in _describe_error("https://nope.invalid", message)


def test_missing_browser_message_tells_how_to_install_it():
    message = "BrowserType.launch: Executable doesn't exist at /ms-playwright/chrome"

    assert "playwright install chromium" in _describe_error("https://a.test", message)


def test_unknown_error_keeps_only_the_first_line():
    message = "Page.goto: something odd\nCall log:\n  - navigating"

    described = _describe_error("https://a.test", message)

    assert described == "Could not explore https://a.test: Page.goto: something odd"


def test_snapshot_command_prints_the_snapshot(site):
    result = runner.invoke(app, ["snapshot", f"{site}/sample_page.html"])

    assert result.exit_code == 0
    assert f"URL: {site}/sample_page.html" in result.output
    assert "Title: Sample Shop" in result.output
    assert "Captured at: " in result.output
    assert 'button "Log in"' in result.output


def test_snapshot_command_shows_a_readable_error_not_a_traceback():
    result = runner.invoke(app, ["snapshot", closed_port_url()])

    assert result.exit_code == 1
    assert "connection refused" in result.output
    assert "Traceback" not in result.output
    assert len(result.output.strip().splitlines()) == 1


def test_snapshot_command_accepts_a_timeout(site):
    result = runner.invoke(app, ["snapshot", f"{site}/slow", "--timeout", "0.5"])

    assert result.exit_code == 1
    assert "timed out after 0.5 seconds" in result.output
