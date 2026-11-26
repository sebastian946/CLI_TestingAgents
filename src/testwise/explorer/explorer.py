"""Opens a page with Playwright and captures its structure. No LLM involved."""

from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, StorageState, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel

from .login_detection import is_login_redirect, is_login_url

DEFAULT_TIMEOUT = 30.0
# After the load event, SPAs keep rendering. Wait for the network to go quiet,
# but only up to this many seconds: pages that poll never become idle.
NETWORK_IDLE_TIMEOUT = 5.0
ALLOWED_SCHEMES = ("http", "https", "file")
INSTALL_HINT = "playwright install chromium"
HTTP_UNAUTHORIZED = 401
VISIBLE_PASSWORD_FIELD = "input[type=password]:visible"

# Chromium network error code -> explanation for the user.
NETWORK_ERRORS = {
    "ERR_NAME_NOT_RESOLVED": "domain not found. Check the URL for typos",
    "ERR_CONNECTION_REFUSED": "connection refused. Is the server running?",
    "ERR_CONNECTION_RESET": "the server closed the connection",
    "ERR_CONNECTION_TIMED_OUT": "the connection timed out",
    "ERR_EMPTY_RESPONSE": "the server sent no response",
    "ERR_INTERNET_DISCONNECTED": "no internet connection",
    "ERR_ADDRESS_UNREACHABLE": "the address is unreachable",
    "ERR_FILE_NOT_FOUND": "file not found",
    "ERR_CERT_": "the site's TLS certificate is not valid",
    "ERR_SSL_": "the TLS connection failed",
}


class ExploreError(Exception):
    """The page could not be explored."""


class LoginRequiredError(ExploreError):
    """The page asked for is behind a login and the browser landed on the login.

    `has_session` is True when a saved session was used and the site rejected
    it anyway, which means that the session has expired.
    """

    def __init__(self, url: str, login_url: str, has_session: bool) -> None:
        self.url = url
        self.login_url = login_url
        self.has_session = has_session
        if has_session:
            message = (
                "The saved session has expired or is not valid for this page. "
                f"Log in again with: testwise auth {url}"
            )
        else:
            message = f"This page requires logging in. Run: testwise auth {url}"
        super().__init__(message)


class PageSnapshot(BaseModel):
    """What the explorer captured from a page."""

    url: str  # Final URL, after redirects.
    title: str
    aria_snapshot: str  # YAML with the roles and names of the page.
    captured_at: datetime  # UTC.


def explore(
    url: str,
    storage_state: str | Path | StorageState | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    check_status: bool = True,
    check_login: bool = True,
) -> PageSnapshot:
    """Open `url` in headless Chromium and return its snapshot.

    `storage_state` is a Playwright storage state (a path or a dict) used to
    explore pages behind a login. `timeout` is in seconds and applies to each
    step (navigation, snapshot).

    Landing on a login screen instead of the page asked for raises
    LoginRequiredError, unless `check_login` is False.

    An HTTP 4xx/5xx response is an error unless `check_status` is False. Some
    single-page apps answer 404 on their inner routes and still render the page.

    Raises ExploreError with a user-facing message if the page cannot be read.
    """
    with open_page(
        url,
        storage_state=storage_state,
        timeout=timeout,
        check_status=check_status,
        check_login=check_login,
    ) as page:
        return PageSnapshot(
            url=page.url,
            title=page.title(),
            aria_snapshot=page.locator("body").aria_snapshot(),
            captured_at=datetime.now(UTC),
        )


@contextmanager
def open_page(
    url: str,
    storage_state: str | Path | StorageState | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    headless: bool = True,
    check_status: bool = True,
    check_login: bool = True,
) -> Generator[Page]:
    """Open `url` in Chromium and yield the loaded page.

    The browser only lives inside the `with` block: use the page there, because
    it is closed on exit. Any Playwright failure, during the navigation or
    inside the block, is raised as an ExploreError with a user-facing message.

    With `check_login`, landing on a login screen instead of the page asked for
    raises LoginRequiredError. With `check_status`, an HTTP 4xx/5xx response is
    an error.
    """
    _check_url(url)
    _check_storage_state(storage_state)
    timeout_ms = timeout * 1000

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            try:
                context = browser.new_context(storage_state=storage_state)
                context.set_default_timeout(timeout_ms)
                page = context.new_page()

                response = page.goto(url, wait_until="load", timeout=timeout_ms)
                # Wait before looking at the URL: single-page apps redirect to
                # their login screen from JavaScript, after the load event.
                _wait_for_network_idle(page, timeout)
                status = response.status if response is not None else 200

                if check_login and _landed_on_login(page, url, status):
                    raise LoginRequiredError(
                        url, page.url, has_session=storage_state is not None
                    )
                if check_status and status >= 400:
                    raise ExploreError(
                        f"Could not explore {url}: the server responded with "
                        f"HTTP {status} {response.status_text}".rstrip()
                        + ". If the page still renders, use --ignore-http-errors."
                    )

                yield page
            finally:
                # The user may have closed the window already.
                with suppress(PlaywrightError):
                    browser.close()
    except PlaywrightTimeoutError:
        raise ExploreError(
            f"Could not explore {url}: timed out after {timeout:g} seconds. "
            "Try a longer timeout."
        ) from None
    except PlaywrightError as error:
        raise ExploreError(_describe_error(url, error.message)) from None


def _landed_on_login(page: Page, url: str, status: int) -> bool:
    if status == HTTP_UNAUTHORIZED:
        return not is_login_url(url)
    has_password_field = page.locator(VISIBLE_PASSWORD_FIELD).count() > 0
    return is_login_redirect(url, page.url, has_password_field)


def _check_url(url: str) -> None:
    scheme = urlparse(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise ExploreError(
            f"Invalid URL '{url}': it must start with http:// or https://."
        )


def _check_storage_state(storage_state: str | Path | StorageState | None) -> None:
    if isinstance(storage_state, str | Path) and not Path(storage_state).is_file():
        raise ExploreError(f"Storage state file not found: {storage_state}")


def _wait_for_network_idle(page, timeout: float) -> None:
    idle_timeout_ms = min(timeout, NETWORK_IDLE_TIMEOUT) * 1000
    try:
        page.wait_for_load_state("networkidle", timeout=idle_timeout_ms)
    except PlaywrightTimeoutError:
        # The page loaded but keeps talking to the network: snapshot it as it is.
        pass


def _describe_error(url: str, message: str) -> str:
    """Turn a Playwright error message into one line the user can act on."""
    if "Executable doesn't exist" in message:
        return f"Chromium is not installed for Playwright. Run: {INSTALL_HINT}"
    for code, explanation in NETWORK_ERRORS.items():
        if code in message:
            return f"Could not explore {url}: {explanation}."
    first_line = message.strip().splitlines()[0] if message.strip() else "unknown error"
    return f"Could not explore {url}: {first_line}"
