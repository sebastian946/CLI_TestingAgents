"""Tells whether a navigation ended on a login screen instead of the page asked for.

Pure functions over URLs: no browser, no LLM. The explorer calls them right
after navigating, so a private page opened without a session is rejected
before any snapshot (and any token) is spent on the login screen.
"""

import re
from urllib.parse import urlparse

# Words that mark a path segment as part of a login flow.
LOGIN_WORDS = frozenset({"login", "logon", "signin", "auth", "sso", "oauth", "oauth2"})
# First label of hosts dedicated to logging in (login.example.com).
LOGIN_HOSTS = frozenset({"login", "signin", "auth", "sso", "accounts"})

_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def is_login_redirect(requested: str, final: str, has_password_field: bool) -> bool:
    """True when asking for `requested` ended on a login screen at `final`.

    A login screen is a redirect that lands either on a page with a visible
    password field or on a URL that looks like a login route.

    When the URLs are the same page there was no redirect, so the user is
    looking at what they asked for, even if that is a login page. The same
    holds when the requested URL is itself a login URL: the user wants that
    screen, wherever the site ends up serving it.
    """
    if same_page(requested, final) or is_login_url(requested):
        return False
    return has_password_field or is_login_url(final)


def same_page(first: str, second: str) -> bool:
    """True when two URLs point to the same page.

    Differences that sites add on their own are ignored: http vs https, a
    leading `www.`, a trailing slash, the query string and in-page anchors.
    """
    return _page_key(first) == _page_key(second)


def is_login_url(url: str) -> bool:
    """True for URLs such as /login, /users/sign-in, /auth/callback or
    login.example.com. Words are matched whole: /authors is not a login URL."""
    parsed = urlparse(url.lower())
    if (parsed.hostname or "").split(".")[0] in LOGIN_HOSTS:
        return True
    segments = parsed.path.split("/") + _route(parsed.fragment).split("/")
    return any(_is_login_segment(segment) for segment in segments)


def _is_login_segment(segment: str) -> bool:
    words = [word for word in _NOT_ALPHANUMERIC.split(segment) if word]
    # "sign-in" and "log_in" are the same words written with a separator.
    return bool(LOGIN_WORDS.intersection(words)) or "".join(words) in LOGIN_WORDS


def _page_key(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.port:
        host += f":{parsed.port}"
    return host, parsed.path.rstrip("/"), _route(parsed.fragment).rstrip("/")


def _route(fragment: str) -> str:
    """The fragment when it is a client-side route (#/login), not an anchor."""
    fragment = fragment.removeprefix("!")
    return fragment if fragment.startswith("/") else ""
