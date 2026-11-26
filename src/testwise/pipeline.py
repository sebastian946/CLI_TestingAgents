"""From a URL to the locators of its page: explore, filter, ask the LLM.

This is the flow behind `testwise locators`. It does not write anything: the
caller decides whether to save the result.
"""

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import StorageState
from pydantic_ai.models import Model

from .explorer import DEFAULT_TIMEOUT, FilterStats, explore, filter_with_stats
from .llm import TokenUsage, generate_locators
from .locators import PageContext
from .storage import slug_from_url


@dataclass(frozen=True)
class GeneratedPage:
    """A page ready to be saved, and what it cost to produce."""

    page: PageContext
    usage: TokenUsage
    model: str
    snapshot_stats: FilterStats


def generate_page(
    url: str,
    *,
    name: str | None = None,
    storage_state: str | Path | StorageState | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    check_status: bool = True,
    check_login: bool = True,
    model: Model | str | None = None,
) -> GeneratedPage:
    """Explore `url` and return its PageContext with locators from the LLM.

    The steps run in order and each one can stop the flow before the next:

    1. Explore: open the page and take its accessibility snapshot. A page that
       needs a login raises LoginRequiredError here, before any token is spent.
    2. Filter: reduce the snapshot to what the LLM needs.
    3. Generate: one call to the LLM, whose answer is validated against the
       Locator model (see `generate_locators`).

    The locators come out as "unverified": checking them against the real page
    is a separate step that does not need the LLM.

    `name` defaults to a slug of the URL. Raises ExploreError, LLMError,
    SettingsError or pydantic's ValidationError (for an invalid `name`).
    """
    snapshot = explore(
        url,
        storage_state=storage_state,
        timeout=timeout,
        check_status=check_status,
        check_login=check_login,
    )
    filtered, snapshot_stats = filter_with_stats(snapshot.aria_snapshot)
    result = generate_locators(filtered, url=snapshot.url, model=model)

    page = PageContext(
        name=name or slug_from_url(url),
        # The URL the browser ended on is the one the locators belong to.
        url=snapshot.url,
        captured_at=snapshot.captured_at,
        locators=result.locators,
    )
    return GeneratedPage(
        page=page,
        usage=result.usage,
        model=result.model,
        snapshot_stats=snapshot_stats,
    )
