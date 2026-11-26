"""Turns a stored locator into a Playwright locator."""

from typing import Any, cast

from playwright.sync_api import Locator as PlaywrightLocator
from playwright.sync_api import Page

from .models import Locator, Strategy


def build_locator(page: Page, locator: Locator) -> PlaywrightLocator:
    """Build the Playwright locator described by `locator` on `page`.

    Each strategy calls one fixed Playwright method with the stored value as a
    plain argument. Nothing from the page.json is ever evaluated as code.

    Texts are matched exactly, so "Log in" does not also match "Log in with
    Google".
    """
    value = locator.value or ""
    match locator.strategy:
        case Strategy.TEST_ID:
            return page.get_by_test_id(value)
        case Strategy.ROLE:
            # The model guarantees a role here. Playwright rejects unknown ones.
            role = cast(Any, locator.role)
            if locator.value is None:
                return page.get_by_role(role)
            return page.get_by_role(role, name=value, exact=True)
        case Strategy.LABEL:
            return page.get_by_label(value, exact=True)
        case Strategy.PLACEHOLDER:
            return page.get_by_placeholder(value, exact=True)
        case Strategy.TEXT:
            return page.get_by_text(value, exact=True)
        case Strategy.CSS:
            # The prefix forces the CSS engine: the value can never switch to
            # another selector engine such as xpath= or text=.
            return page.locator(f"css={value}")
    raise ValueError(f"Unknown strategy: {locator.strategy!r}")
