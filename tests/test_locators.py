from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from testwise.locators import (
    SCHEMA_VERSION,
    Locator,
    LocatorStatus,
    PageContext,
    Strategy,
    build_locator,
)

FIXTURE = Path(__file__).parent / "fixtures" / "locators_page.html"
CAPTURED_AT = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)


def make_page(**changes) -> PageContext:
    fields = {
        "name": "login",
        "url": "https://app.com/login",
        "captured_at": CAPTURED_AT,
    }
    return PageContext(**(fields | changes))


# --- Locator ----------------------------------------------------------------


def test_locator_from_the_example_in_the_ticket():
    locator = Locator.model_validate(
        {
            "name": "email_input",
            "description": "Campo de correo del formulario de login",
            "strategy": "role",
            "role": "textbox",
            "value": "Email",
            "status": "ok",
            "last_verified": "2026-11-24T15:00:00Z",
        }
    )

    assert locator.strategy is Strategy.ROLE
    assert locator.role == "textbox"
    assert locator.status is LocatorStatus.OK
    assert locator.last_verified == CAPTURED_AT


def test_new_locator_starts_unverified():
    locator = Locator(name="submit", strategy=Strategy.TEXT, value="Send")

    assert locator.status is LocatorStatus.UNVERIFIED
    assert locator.last_verified is None
    assert locator.description == ""


def test_strategies_are_the_six_of_the_ticket():
    assert {strategy.value for strategy in Strategy} == {
        "test_id",
        "role",
        "label",
        "placeholder",
        "text",
        "css",
    }


def test_role_strategy_needs_a_role():
    with pytest.raises(ValidationError, match="strategy 'role' needs a 'role'"):
        Locator(name="submit", strategy="role", value="Send")


def test_role_strategy_works_without_a_name():
    locator = Locator(name="main_region", strategy="role", role="main")

    assert locator.value is None


@pytest.mark.parametrize("strategy", ["test_id", "label", "placeholder", "text", "css"])
def test_other_strategies_need_a_value(strategy):
    with pytest.raises(ValidationError, match=f"strategy '{strategy}' needs"):
        Locator(name="thing", strategy=strategy)
    with pytest.raises(ValidationError, match=f"strategy '{strategy}' needs"):
        Locator(name="thing", strategy=strategy, value="   ")


def test_role_field_is_rejected_with_another_strategy():
    with pytest.raises(ValidationError, match="'role' is only used with strategy"):
        Locator(name="thing", strategy="css", role="button", value=".primary")


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValidationError, match="strategy"):
        Locator(name="thing", strategy="xpath", value="//button")


def test_code_is_not_a_field():
    with pytest.raises(ValidationError, match="expression"):
        Locator(
            name="thing",
            strategy="css",
            value=".primary",
            expression="page.locator('.primary')",
        )


@pytest.mark.parametrize("name", ["Email Input", "email-input", "1st", "", "émail"])
def test_locator_name_must_be_an_identifier(name):
    with pytest.raises(ValidationError, match="name"):
        Locator(name=name, strategy="text", value="Send")


def test_dates_without_a_time_zone_are_rejected():
    with pytest.raises(ValidationError, match="last_verified"):
        Locator(
            name="thing",
            strategy="text",
            value="Send",
            last_verified="2026-11-24T15:00:00",
        )


# --- PageContext ------------------------------------------------------------


def test_page_gets_the_current_schema_version():
    assert make_page().schema_version == SCHEMA_VERSION == 1


def test_page_from_a_newer_testwise_is_rejected():
    with pytest.raises(ValidationError, match="Update testwise"):
        make_page(schema_version=SCHEMA_VERSION + 1)


@pytest.mark.parametrize("version", [0, -1])
def test_schema_version_starts_at_one(version):
    with pytest.raises(ValidationError, match="schema_version"):
        make_page(schema_version=version)


def test_locator_names_are_unique_in_a_page():
    locators = [
        Locator(name="submit", strategy="text", value="Send"),
        Locator(name="submit", strategy="css", value=".primary"),
    ]

    with pytest.raises(ValidationError, match="two locators are named 'submit'"):
        make_page(locators=locators)


@pytest.mark.parametrize("name", ["Login", "log in", "login_page", "../x", "", "-a"])
def test_page_name_must_be_a_slug(name):
    with pytest.raises(ValidationError, match="name"):
        make_page(name=name)


def test_page_url_must_be_a_web_address():
    with pytest.raises(ValidationError, match="must start with http"):
        make_page(url="app.com/login")


def test_page_finds_a_locator_by_name():
    submit = Locator(name="submit", strategy="text", value="Send")
    page = make_page(locators=[submit])

    assert page.locator("submit") is submit
    with pytest.raises(KeyError):
        page.locator("missing")


# --- build_locator ----------------------------------------------------------


@pytest.fixture(scope="module")
def browser_page():
    """A real page with one element for each strategy."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content(FIXTURE.read_text(encoding="utf-8"))
        yield page
        browser.close()


def tag(browser_page, **locator_fields) -> str:
    """Build the locator and say which element of the page it found."""
    found = build_locator(browser_page, Locator(name="element", **locator_fields))
    assert found.count() == 1
    return found.evaluate("element => element.tagName + '#' + element.id")


def test_build_by_test_id(browser_page):
    assert tag(browser_page, strategy="test_id", value="email-input") == "INPUT#email"


def test_build_by_role_and_name(browser_page):
    assert tag(browser_page, strategy="role", role="textbox", value="Email") == (
        "INPUT#email"
    )


def test_build_by_role_without_a_name(browser_page):
    assert tag(browser_page, strategy="role", role="navigation") == "NAV#"


def test_build_by_label(browser_page):
    assert tag(browser_page, strategy="label", value="Coupon code") == "INPUT#coupon"


def test_build_by_placeholder(browser_page):
    assert tag(browser_page, strategy="placeholder", value="Enter your coupon") == (
        "INPUT#coupon"
    )


def test_build_by_text(browser_page):
    assert tag(
        browser_page, strategy="text", value="Free shipping on orders over $50"
    ) == ("P#")


def test_build_by_css(browser_page):
    assert tag(browser_page, strategy="css", value="button.primary") == "BUTTON#"


def test_names_are_matched_exactly(browser_page):
    # "Log in" must not also match "Log in with Google".
    locator = Locator(name="login", strategy="role", role="button", value="Log in")

    found = build_locator(browser_page, locator)

    assert found.count() == 1
    assert found.inner_text() == "Log in"


def test_css_value_cannot_switch_to_another_selector_engine(browser_page):
    # As a raw selector, "text=Cart" would find the link. As CSS it finds nothing.
    locator = Locator(name="cart", strategy="css", value="text=Cart")

    with pytest.raises(Exception, match="(?i)selector|parsing|unexpected"):
        build_locator(browser_page, locator).count()


def test_locator_that_matches_nothing_is_built_but_finds_no_element(browser_page):
    locator = Locator(name="ghost", strategy="test_id", value="does-not-exist")

    assert build_locator(browser_page, locator).count() == 0
