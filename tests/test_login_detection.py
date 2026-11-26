import json

import pytest
from typer.testing import CliRunner

from testwise.cli import app
from testwise.explorer import ExploreError, LoginRequiredError, explore
from testwise.explorer.login_detection import (
    is_login_redirect,
    is_login_url,
    same_page,
)

runner = CliRunner()
EMPTY_STATE = {"cookies": [], "origins": []}
EXPIRED_STATE = {
    "cookies": [
        {
            "name": "session",
            "value": "old-and-expired",
            "domain": "127.0.0.1",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        }
    ],
    "origins": [],
}


def save_state(project, name: str, state: dict) -> None:
    folder = project / ".testwise" / "auth"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(state), encoding="utf-8")


# --- URLs (pure functions) --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.test/login",
        "https://shop.test/Login.aspx",
        "https://shop.test/users/sign-in",
        "https://shop.test/account/signin?next=/cart",
        "https://shop.test/log_in",
        "https://shop.test/auth/callback",
        "https://shop.test/oauth2/authorize",
        "https://shop.test/sso",
        "https://login.shop.test/",
        "https://accounts.shop.test/start",
        "https://shop.test/#/login",
        "https://shop.test/app#!/auth",
    ],
)
def test_login_urls_are_recognized(url):
    assert is_login_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.test/",
        "https://shop.test/products",
        "https://shop.test/authors/list",
        "https://shop.test/blog/logins-explained",
        "https://shop.test/catalog#login",
        "https://shop.test/search?q=login",
        "https://blogin.test/",
    ],
)
def test_other_urls_are_not_login_urls(url):
    assert not is_login_url(url)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("http://shop.test/cart", "https://shop.test/cart"),
        ("https://shop.test/cart", "https://www.shop.test/cart"),
        ("https://shop.test/cart", "https://shop.test/cart/"),
        ("https://shop.test", "https://shop.test/"),
        ("https://shop.test/cart", "https://shop.test/cart?utm_source=mail"),
        ("https://shop.test/cart", "https://shop.test/cart#summary"),
        ("https://SHOP.test/cart", "https://shop.test/cart"),
    ],
)
def test_same_page_ignores_what_sites_change_on_their_own(first, second):
    assert same_page(first, second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://shop.test/cart", "https://shop.test/login"),
        ("https://shop.test/cart", "https://other.test/cart"),
        ("https://shop.test:8080/cart", "https://shop.test:9090/cart"),
        ("https://shop.test/#/cart", "https://shop.test/#/login"),
    ],
)
def test_same_page_tells_different_pages_apart(first, second):
    assert not same_page(first, second)


def test_redirect_to_a_page_with_a_password_field_is_a_login():
    assert is_login_redirect(
        "https://shop.test/orders", "https://shop.test/", has_password_field=True
    )


def test_redirect_to_a_login_url_is_a_login_even_without_a_password_field():
    assert is_login_redirect(
        "https://shop.test/orders", "https://shop.test/login", has_password_field=False
    )


def test_redirect_to_an_ordinary_page_is_not_a_login():
    assert not is_login_redirect(
        "https://shop.test/old", "https://shop.test/new", has_password_field=False
    )


def test_no_redirect_is_never_a_login():
    url = "https://shop.test/login"

    assert not is_login_redirect(url, url, has_password_field=True)
    assert not is_login_redirect(url, f"{url}/", has_password_field=True)


def test_asking_for_a_login_url_is_never_a_login_redirect():
    assert not is_login_redirect(
        "https://shop.test/login",
        "https://auth.shop.test/signin?client=shop",
        has_password_field=True,
    )


# --- In the browser ---------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "login_path"),
    [
        ("/account", "/login"),  # Redirect to a login URL with a password field.
        ("/dashboard", "/welcome"),  # Password field, URL without login words.
        ("/orders", "/auth/start"),  # Login URL, no password field yet.
        ("/app", "/welcome"),  # Redirect made by JavaScript after loading.
    ],
)
def test_private_page_without_a_session_requires_login(site, path, login_path):
    with pytest.raises(LoginRequiredError) as error:
        explore(f"{site}{path}")

    assert error.value.url == f"{site}{path}"
    assert error.value.login_url == f"{site}{login_path}"
    assert not error.value.has_session
    assert str(error.value) == (
        f"This page requires logging in. Run: testwise auth {site}{path}"
    )


def test_private_page_that_answers_401_requires_login(site):
    with pytest.raises(LoginRequiredError, match="requires logging in"):
        explore(f"{site}/private")


def test_login_required_is_an_explore_error(site):
    with pytest.raises(ExploreError):
        explore(f"{site}/account")


def test_login_page_asked_for_directly_is_explored(site):
    snapshot = explore(f"{site}/login")

    assert snapshot.url == f"{site}/login"
    assert 'button "Log in"' in snapshot.aria_snapshot


def test_page_with_a_password_field_asked_for_directly_is_explored(site):
    assert explore(f"{site}/welcome").title == "Sign in"


def test_redirect_to_an_ordinary_page_is_explored(site):
    assert explore(f"{site}/redirect").title == "Landing"


def test_hidden_password_field_does_not_count(site):
    assert explore(f"{site}/promo").title == "Promo"


def test_valid_session_reaches_the_private_page(site):
    state = json.loads(json.dumps(EXPIRED_STATE))
    state["cookies"][0]["value"] = "abc123"

    for path in ("/account", "/dashboard", "/app", "/private"):
        assert explore(f"{site}{path}", state).title in ("Account", "App")


@pytest.mark.parametrize("path", ["/account", "/app", "/private"])
def test_expired_session_asks_to_log_in_again(site, path):
    with pytest.raises(LoginRequiredError) as error:
        explore(f"{site}{path}", EXPIRED_STATE)

    assert error.value.has_session
    assert "session has expired" in str(error.value)
    assert f"testwise auth {site}{path}" in str(error.value)


def test_login_check_can_be_turned_off(site):
    # /moved redirects everyone to a public page that has a login box.
    with pytest.raises(LoginRequiredError):
        explore(f"{site}/moved")

    assert explore(f"{site}/moved", check_login=False).title == "Sample Shop"


# --- Commands ---------------------------------------------------------------


def test_snapshot_of_a_private_page_stops_with_the_command_to_run(project, site):
    result = runner.invoke(app, ["snapshot", f"{site}/account"])

    assert result.exit_code == 1
    assert "This page requires logging in." in result.output
    assert f"Run: testwise auth {site}/account" in result.output
    assert "--auth default" in result.output
    # Nothing of the login screen is printed: there is no snapshot to send on.
    assert "Title:" not in result.output
    assert "Log in" not in result.output
    assert "Traceback" not in result.output


def test_snapshot_of_the_login_page_itself_works(project, site):
    result = runner.invoke(app, ["snapshot", f"{site}/login"])

    assert result.exit_code == 0
    assert "Title: Sign in" in result.output


def test_snapshot_with_an_expired_session_asks_to_repeat_auth(project, site):
    save_state(project, "default", EXPIRED_STATE)

    result = runner.invoke(app, ["snapshot", f"{site}/account", "--auth", "default"])

    assert result.exit_code == 1
    assert "The saved session 'default' has expired" in result.output
    assert f"Log in again with: testwise auth {site}/account" in result.output
    assert "--name" not in result.output


def test_expired_named_session_keeps_its_name_in_the_command(project, site):
    save_state(project, "admin", EMPTY_STATE)

    result = runner.invoke(app, ["snapshot", f"{site}/account", "--auth", "admin"])

    assert result.exit_code == 1
    assert "The saved session 'admin' has expired" in result.output
    assert f"testwise auth {site}/account --name admin" in result.output


def test_snapshot_can_skip_the_login_check(project, site):
    result = runner.invoke(app, ["snapshot", f"{site}/moved", "--skip-login-check"])

    assert result.exit_code == 0
    assert "Title: Sample Shop" in result.output
