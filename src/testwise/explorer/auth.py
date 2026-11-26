"""Saved sessions: the user logs in by hand once and testwise reuses the session.

Credentials are typed by the user in a real browser window. Neither testwise
nor the LLM ever see them: only the resulting session (cookies and local
storage) is saved, in the user's project.
"""

import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from ..scaffold import ensure_gitignore
from .explorer import DEFAULT_TIMEOUT, ExploreError, open_page

AUTH_DIR = Path(".testwise") / "auth"
DEFAULT_SESSION = "default"
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class AuthError(ExploreError):
    """A session could not be saved or loaded."""


def session_path(name: str = DEFAULT_SESSION, root: Path | None = None) -> Path:
    """Path of the session called `name`: <root>/.testwise/auth/<name>.json."""
    if not _VALID_NAME.match(name):
        raise AuthError(
            f"Invalid session name '{name}': use only letters, numbers, "
            "hyphens and underscores."
        )
    return (root or Path.cwd()) / AUTH_DIR / f"{name}.json"


def load_session(name: str = DEFAULT_SESSION, root: Path | None = None) -> Path:
    """Path of an existing session, ready to pass as `storage_state`."""
    path = session_path(name, root)
    if not path.is_file():
        raise AuthError(
            f"No saved session named '{name}'. "
            f"Create it with: testwise auth <url> --name {name}"
        )
    return path


def save_session(
    url: str,
    name: str = DEFAULT_SESSION,
    *,
    wait_for_login: Callable[[Page], None],
    timeout: float = DEFAULT_TIMEOUT,
    headless: bool = False,
    root: Path | None = None,
) -> Path:
    """Open `url` in a visible browser, let the user log in and save the session.

    `wait_for_login` receives the page and must return once the user has
    finished logging in (the CLI waits for Enter). It can take as long as the
    login needs, including two-factor authentication.

    Returns the path of the saved file. It contains session cookies, so the
    folder is added to the project's .gitignore before anything is written.
    """
    path = session_path(name, root)
    ensure_gitignore((root or Path.cwd()) / ".gitignore")

    # A login page may answer 401/403 before the user logs in: not an error here.
    with open_page(
        url, timeout=timeout, headless=headless, check_status=False, check_login=False
    ) as page:
        wait_for_login(page)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.context.storage_state(path=path)
        except PlaywrightError:
            raise AuthError(
                "The browser was closed before the session was saved. Run the "
                "command again and press Enter while the browser is still open."
            ) from None

    # Readable only by the owner where the system supports it.
    with suppress(OSError):
        path.chmod(0o600)
    return path
