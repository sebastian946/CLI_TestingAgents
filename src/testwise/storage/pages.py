"""Reads and writes page.json files in .testwise/pages/.

Unlike the sessions in .testwise/auth/, these files hold no secrets and are
meant to be committed: they are what testwise remembers about each page.
"""

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

from ..locators.models import PAGE_NAME_PATTERN, PageContext

PAGES_DIR = Path(".testwise") / "pages"
PAGE_SUFFIX = ".page.json"
HOME_NAME = "home"
MAX_NAME_LENGTH = 80

_VALID_NAME = re.compile(PAGE_NAME_PATTERN)
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_FILE_EXTENSION = re.compile(r"\.[a-z0-9]{2,5}$")


class PageFileError(Exception):
    """A page.json could not be read or written."""


def slug_from_url(url: str) -> str:
    """Derive the name of a page from its URL.

    https://app.com/login -> login
    https://app.com/products/shoes.html -> products-shoes
    https://app.com/ -> home

    The domain and the query string are left out, so the name stays short and
    does not change between environments (staging, production).
    """
    parsed = urlparse(url)
    fragment = parsed.fragment.removeprefix("!")
    route = fragment if fragment.startswith("/") else ""  # Client-side routes.
    path = _FILE_EXTENSION.sub("", parsed.path.lower()) + "/" + route.lower()
    # Split accented letters into letter + accent, then keep only ASCII.
    ascii_path = unicodedata.normalize("NFKD", path).encode("ascii", "ignore").decode()
    slug = _NOT_ALPHANUMERIC.sub("-", ascii_path).strip("-")
    return slug[:MAX_NAME_LENGTH].rstrip("-") or HOME_NAME


def page_path(name: str, root: Path | None = None) -> Path:
    """Path of the page called `name`: <root>/.testwise/pages/<name>.page.json."""
    if not _VALID_NAME.match(name):
        raise PageFileError(
            f"Invalid page name '{name}': use lowercase letters, numbers and "
            "hyphens (for example checkout-payment)."
        )
    return (root or Path.cwd()) / PAGES_DIR / f"{name}{PAGE_SUFFIX}"


def save_page(page: PageContext, root: Path | None = None) -> Path:
    """Write `page` to its page.json, replacing any previous version."""
    path = page_path(page.name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fields without a value are left out to keep the file easy to read.
    text = page.model_dump_json(indent=2, exclude_none=True) + "\n"
    # Write to a temporary file first: a crash never leaves a half-written page.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return path


def load_page(name: str, root: Path | None = None) -> PageContext:
    """Read the page called `name`. Raises PageFileError if it cannot be read."""
    path = page_path(name, root)
    if not path.is_file():
        raise PageFileError(f"No saved page named '{name}' (expected {path}).")
    return load_page_file(path)


def load_page_file(path: Path) -> PageContext:
    """Read a page.json from any path, with errors meant for the user."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PageFileError(f"Could not read {path}: {error.strerror}") from None
    except json.JSONDecodeError as error:
        raise PageFileError(
            f"{path.name} is not valid JSON: {error.msg} "
            f"(line {error.lineno}, column {error.colno})."
        ) from None
    try:
        return PageContext.model_validate(data)
    except ValidationError as error:
        problems = "\n".join(f"  - {line}" for line in describe_errors(error))
        raise PageFileError(
            f"{path.name} is not a valid page file:\n{problems}"
        ) from None


def list_pages(root: Path | None = None) -> list[str]:
    """Names of the saved pages, in alphabetical order."""
    folder = (root or Path.cwd()) / PAGES_DIR
    return sorted(
        file.name.removesuffix(PAGE_SUFFIX) for file in folder.glob(f"*{PAGE_SUFFIX}")
    )


def describe_errors(error: ValidationError) -> list[str]:
    """One readable line per problem: where it is and what is wrong."""
    lines = []
    for item in error.errors(include_url=False):
        location = _location(item["loc"])
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location}: {message}" if location else message)
    return lines


def _location(parts: tuple[int | str, ...]) -> str:
    """("locators", 0, "strategy") -> locators[0].strategy"""
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text
