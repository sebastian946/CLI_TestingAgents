import json
from datetime import UTC, datetime

import pytest

from testwise.locators import Locator, LocatorStatus, PageContext, Strategy
from testwise.storage import (
    PageFileError,
    list_pages,
    load_page,
    load_page_file,
    page_path,
    save_page,
    slug_from_url,
)

CAPTURED_AT = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
TICKET_EXAMPLE = {
    "schema_version": 1,
    "name": "login",
    "url": "https://app.com/login",
    "captured_at": "2026-11-24T15:00:00Z",
    "locators": [
        {
            "name": "email_input",
            "description": "Campo de correo del formulario de login",
            "strategy": "role",
            "role": "textbox",
            "value": "Email",
            "status": "ok",
            "last_verified": "2026-11-24T15:00:00Z",
        }
    ],
}


def full_page() -> PageContext:
    """A page with one locator of each strategy and every field in use."""
    return PageContext(
        name="checkout-payment",
        url="https://app.com/checkout/payment",
        captured_at=CAPTURED_AT,
        locators=[
            Locator(
                name="email_input",
                description="Campo de correo del formulario de login",
                strategy=Strategy.ROLE,
                role="textbox",
                value="Email",
                status=LocatorStatus.OK,
                last_verified=CAPTURED_AT,
            ),
            Locator(name="main_region", strategy=Strategy.ROLE, role="main"),
            Locator(name="pay_button", strategy=Strategy.TEST_ID, value="pay"),
            Locator(name="card_number", strategy=Strategy.LABEL, value="Card number"),
            Locator(name="coupon", strategy=Strategy.PLACEHOLDER, value="Coupon"),
            Locator(
                name="total",
                strategy=Strategy.TEXT,
                value='Total: "$10" — envío año',
                status=LocatorStatus.BROKEN,
                last_verified=CAPTURED_AT,
            ),
            Locator(name="summary", strategy=Strategy.CSS, value="aside > .summary"),
        ],
    )


def write_page_file(project, name: str, content: str):
    path = page_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- Saving and loading -----------------------------------------------------


def test_page_is_saved_in_the_pages_folder(project):
    path = save_page(full_page())

    assert path == project / ".testwise" / "pages" / "checkout-payment.page.json"
    assert path.is_file()


def test_saved_page_is_read_back_without_losing_data(project):
    page = full_page()

    save_page(page)

    assert load_page("checkout-payment") == page


def test_empty_page_survives_the_round_trip(project):
    page = PageContext(name="home", url="https://app.com/", captured_at=CAPTURED_AT)

    save_page(page)

    assert load_page("home") == page


def test_file_has_the_format_of_the_ticket(project):
    page = PageContext.model_validate(TICKET_EXAMPLE)

    path = save_page(page)

    assert json.loads(path.read_text(encoding="utf-8")) == TICKET_EXAMPLE
    assert load_page("login") == page


def test_file_is_readable_by_people(project):
    text = save_page(full_page()).read_text(encoding="utf-8")

    assert text.startswith('{\n  "schema_version": 1,\n  "name": "checkout-payment",')
    assert text.endswith("}\n")
    # Accents are written as they are, not as \u escapes.
    assert "envío año" in text
    # Empty fields are left out.
    assert "null" not in text


def test_saving_again_replaces_the_file_and_leaves_no_temporary_files(project):
    page = full_page()
    save_page(page)
    page.locators = page.locators[:1]

    path = save_page(page)

    assert len(load_page("checkout-payment").locators) == 1
    assert [file.name for file in path.parent.iterdir()] == [path.name]


def test_pages_are_listed_by_name(project):
    assert list_pages() == []

    save_page(full_page())
    save_page(PageContext(name="home", url="https://app.com/", captured_at=CAPTURED_AT))

    assert list_pages() == ["checkout-payment", "home"]


@pytest.mark.parametrize("name", ["../secrets", "a/b", "Login", "", "a b"])
def test_page_name_cannot_escape_the_pages_folder(project, name):
    with pytest.raises(PageFileError, match="Invalid page name"):
        page_path(name)


# --- Errors -----------------------------------------------------------------


def test_missing_page_is_reported(project):
    with pytest.raises(PageFileError, match="No saved page named 'login'"):
        load_page("login")


def test_broken_json_says_where(project):
    write_page_file(project, "login", '{\n  "name": "login",\n  "url": \n}')

    with pytest.raises(PageFileError) as error:
        load_page("login")

    message = str(error.value)
    assert "login.page.json is not valid JSON" in message
    assert "line 4" in message
    assert "Traceback" not in message


def test_invalid_content_lists_every_problem_and_where_it_is(project):
    data = json.loads(json.dumps(TICKET_EXAMPLE))
    data["locators"][0]["strategy"] = "xpath"
    data["locators"].append({"name": "Pay Button", "strategy": "css", "value": "#pay"})
    del data["url"]
    write_page_file(project, "login", json.dumps(data))

    with pytest.raises(PageFileError) as error:
        load_page("login")

    message = str(error.value)
    assert message.startswith("login.page.json is not a valid page file:\n  - ")
    assert "url: Field required" in message
    assert "locators[0].strategy: Input should be 'test_id', 'role'" in message
    assert "locators[1].name: " in message
    # Plain lines for the user: no pydantic internals.
    assert "pydantic" not in message
    assert "type=" not in message


def test_missing_strategy_argument_is_explained(project):
    data = json.loads(json.dumps(TICKET_EXAMPLE))
    del data["locators"][0]["role"]
    write_page_file(project, "login", json.dumps(data))

    with pytest.raises(PageFileError, match=r"locators\[0\]: strategy 'role' needs"):
        load_page("login")


def test_unknown_field_is_reported_instead_of_dropped(project):
    data = json.loads(json.dumps(TICKET_EXAMPLE))
    data["locators"][0]["expression"] = "page.locator('#x')"
    write_page_file(project, "login", json.dumps(data))

    with pytest.raises(PageFileError, match=r"locators\[0\].expression: Extra inputs"):
        load_page("login")


def test_file_from_a_newer_version_asks_to_update(project):
    data = json.loads(json.dumps(TICKET_EXAMPLE)) | {"schema_version": 99}
    write_page_file(project, "login", json.dumps(data))

    with pytest.raises(PageFileError, match="schema_version: this file uses schema"):
        load_page("login")


def test_file_that_is_not_an_object_is_reported(project):
    path = write_page_file(project, "login", "[1, 2, 3]")

    with pytest.raises(PageFileError, match="not a valid page file"):
        load_page_file(path)


# --- Name from the URL ------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://app.com/login", "login"),
        ("https://app.com/login/", "login"),
        ("https://app.com/", "home"),
        ("https://app.com", "home"),
        ("https://app.com/products/shoes", "products-shoes"),
        ("https://www.saucedemo.com/inventory.html", "inventory"),
        ("https://app.com/Checkout/Step_2.aspx", "checkout-step-2"),
        ("https://app.com/login?next=/cart&utm=x", "login"),
        ("https://app.com/docs#installation", "docs"),
        ("https://app.com/#/settings/profile", "settings-profile"),
        ("https://app.com/app#!/inbox", "app-inbox"),
        ("https://app.com/orders/1234", "orders-1234"),
        ("https://app.com/catálogo/niños", "catalogo-ninos"),
        ("https://app.com/a%20b/c..d", "a-20b-c-d"),
        ("https://staging.app.com/login", "login"),
        ("http://localhost:3000/dashboard", "dashboard"),
    ],
)
def test_slug_from_url(url, name):
    assert slug_from_url(url) == name


def test_slug_is_always_a_valid_page_name(project):
    urls = [
        "https://app.com/",
        "https://app.com/---/",
        "https://app.com/" + "very-long-section/" * 20,
        "https://app.com/日本語",
        "https://app.com/.hidden",
    ]

    for url in urls:
        name = slug_from_url(url)
        assert len(name) <= 80
        assert page_path(name).name == f"{name}.page.json"


def test_page_named_from_its_url_can_be_saved_and_loaded(project):
    url = "https://www.saucedemo.com/inventory.html"
    page = PageContext(name=slug_from_url(url), url=url, captured_at=CAPTURED_AT)

    save_page(page)

    assert load_page("inventory") == page
