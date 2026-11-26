"""Integration tests of `testwise locators`: a real browser on a local page,
with a simulated LLM."""

import json
from functools import partial

import pytest
from fakes import ScriptedModel
from typer.testing import CliRunner

from testwise import pipeline
from testwise.cli import app
from testwise.explorer import LoginRequiredError
from testwise.explorer.explorer import open_page
from testwise.llm import LLMError
from testwise.locators import LocatorStatus, build_locator
from testwise.pipeline import generate_page
from testwise.storage import load_page, page_path

runner = CliRunner()

# What the model would answer for tests/fixtures/sample_page.html.
SAMPLE_LOCATORS = [
    {
        "name": "username_input",
        "description": "Field for the user name.",
        "strategy": "role",
        "role": "textbox",
        "value": "Username",
    },
    {
        "name": "password_input",
        "description": "Field for the password.",
        "strategy": "role",
        "role": "textbox",
        "value": "Password",
    },
    {
        "name": "login_button",
        "description": "Submits the login form.",
        "strategy": "role",
        "role": "button",
        "value": "Log in",
    },
    {
        "name": "about_link",
        "description": "Opens the page about the shop.",
        "strategy": "text",
        "value": "About us",
    },
]
ACCOUNT_LOCATORS = [
    {
        "name": "welcome_heading",
        "description": "Confirms that the user is logged in.",
        "strategy": "role",
        "role": "heading",
        "value": "Welcome back",
    }
]
VALID_SESSION = {
    "cookies": [
        {
            "name": "session",
            "value": "abc123",
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


@pytest.fixture
def fake_llm(project, monkeypatch):
    """Configure a model in the project and replace the LLM with a script.

    Returns a function: call it with the answers the model must give. The
    ScriptedModel it returns records every request made to the LLM.
    """
    (project / ".env").write_text(
        "TESTWISE_MODEL=anthropic:claude-test\nANTHROPIC_API_KEY=sk-ant-fake\n",
        encoding="utf-8",
    )

    def install(*responses) -> ScriptedModel:
        scripted = ScriptedModel(*responses)
        monkeypatch.setattr(
            pipeline, "generate_page", partial(generate_page, model=scripted.model)
        )
        return scripted

    return install


def saved_file(project, name: str = "sample-page"):
    return project / ".testwise" / "pages" / f"{name}.page.json"


# --- The flow, without the command ------------------------------------------


def test_generate_page_chains_explore_filter_and_llm(site):
    scripted = ScriptedModel(SAMPLE_LOCATORS)
    url = f"{site}/sample_page.html"

    generated = generate_page(url, model=scripted.model)

    page = generated.page
    assert page.name == "sample-page"
    assert page.url == url
    assert page.captured_at is not None
    assert [locator.name for locator in page.locators] == [
        "username_input",
        "password_input",
        "login_button",
        "about_link",
    ]
    assert all(locator.status is LocatorStatus.UNVERIFIED for locator in page.locators)
    assert generated.usage.requests == 1
    assert generated.usage.input_tokens > 0
    assert generated.snapshot_stats.raw_chars > generated.snapshot_stats.filtered_chars


def test_the_llm_receives_the_filtered_snapshot_of_the_real_page(site):
    scripted = ScriptedModel(SAMPLE_LOCATORS)

    generate_page(f"{site}/sample_page.html", model=scripted.model)

    prompt = scripted.first_prompt
    assert f"Page URL: {site}/sample_page.html" in prompt
    assert 'button "Log in"' in prompt
    assert 'textbox "Username"' in prompt
    # The filter removes link URLs before the LLM sees the snapshot.
    assert "/url" not in prompt


def test_generate_page_writes_nothing(project, site):
    generate_page(
        f"{site}/sample_page.html", model=ScriptedModel(SAMPLE_LOCATORS).model
    )

    assert not (project / ".testwise").exists()


def test_page_behind_a_login_never_reaches_the_llm(site):
    scripted = ScriptedModel(SAMPLE_LOCATORS)

    with pytest.raises(LoginRequiredError):
        generate_page(f"{site}/account", model=scripted.model)

    assert scripted.requests == []


def test_generated_locators_find_their_elements_on_the_real_page(site):
    url = f"{site}/sample_page.html"
    page = generate_page(url, model=ScriptedModel(SAMPLE_LOCATORS).model).page

    with open_page(url) as browser_page:
        found = {
            locator.name: build_locator(browser_page, locator).count()
            for locator in page.locators
        }

    assert found == {
        "username_input": 1,
        "password_input": 1,
        "login_button": 1,
        "about_link": 1,
    }


# --- The command ------------------------------------------------------------


def test_locators_command_saves_the_page_json(project, site, fake_llm):
    scripted = fake_llm(SAMPLE_LOCATORS)
    url = f"{site}/sample_page.html"

    result = runner.invoke(app, ["locators", url])

    assert result.exit_code == 0, result.output
    data = json.loads(saved_file(project).read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["name"] == "sample-page"
    assert data["url"] == url
    assert [locator["name"] for locator in data["locators"]] == [
        "username_input",
        "password_input",
        "login_button",
        "about_link",
    ]
    assert load_page("sample-page").locator("login_button").value == "Log in"
    # One call to the LLM when the answer is valid.
    assert len(scripted.requests) == 1


def test_locators_command_prints_a_table_and_the_tokens(project, site, fake_llm):
    fake_llm(SAMPLE_LOCATORS)

    result = runner.invoke(app, ["locators", f"{site}/sample_page.html"])

    lines = result.output.splitlines()
    header = next(line for line in lines if line.startswith("Name"))
    assert header.split() == ["Name", "Strategy", "Target", "Status"]
    row = next(line for line in lines if line.startswith("login_button"))
    assert row.split(maxsplit=2)[:2] == ["login_button", "role"]
    assert 'button "Log in"' in row
    assert row.endswith("unverified")
    about = next(line for line in lines if line.startswith("about_link"))
    assert about.split() == ["about_link", "text", "About", "us", "unverified"]
    # Columns are aligned.
    assert header.index("Status") == row.index("unverified")
    assert "Saved 4 locators to" in result.output
    assert "Tokens used: " in result.output
    assert "in 1 request(s)" in result.output
    assert "Snapshot: " in result.output


def test_name_option_sets_the_page_name(project, site, fake_llm):
    fake_llm(SAMPLE_LOCATORS)

    result = runner.invoke(
        app, ["locators", f"{site}/sample_page.html", "--name", "shop-login"]
    )

    assert result.exit_code == 0
    assert saved_file(project, "shop-login").is_file()
    assert not saved_file(project).exists()
    assert load_page("shop-login").name == "shop-login"


def test_invalid_name_is_rejected_before_doing_anything(project, site, fake_llm):
    scripted = fake_llm(SAMPLE_LOCATORS)

    result = runner.invoke(
        app, ["locators", f"{site}/sample_page.html", "--name", "../x"]
    )

    assert result.exit_code == 1
    assert "Invalid page name" in result.output
    assert scripted.requests == []


def test_existing_page_is_not_touched_without_force(project, site, fake_llm):
    scripted = fake_llm(SAMPLE_LOCATORS)
    url = f"{site}/sample_page.html"
    runner.invoke(app, ["locators", url])
    before = saved_file(project).read_bytes()

    result = runner.invoke(app, ["locators", url])

    assert result.exit_code == 1
    assert "The page 'sample-page' already exists" in result.output
    assert "--check" in result.output
    assert "--force" in result.output
    assert saved_file(project).read_bytes() == before
    # The second run stopped before exploring or calling the LLM.
    assert len(scripted.requests) == 1
    assert "Exploring" not in result.output


def test_force_replaces_the_existing_page(project, site, fake_llm):
    url = f"{site}/sample_page.html"
    fake_llm(SAMPLE_LOCATORS)
    runner.invoke(app, ["locators", url])
    fake_llm(SAMPLE_LOCATORS[:2])

    result = runner.invoke(app, ["locators", url, "--force"])

    assert result.exit_code == 0
    assert "Saved 2 locators to" in result.output
    assert len(load_page("sample-page").locators) == 2


def test_dry_run_runs_everything_but_writes_nothing(project, site, fake_llm):
    scripted = fake_llm(SAMPLE_LOCATORS)

    result = runner.invoke(app, ["locators", f"{site}/sample_page.html", "--dry-run"])

    assert result.exit_code == 0
    assert len(scripted.requests) == 1
    assert "login_button" in result.output
    assert "Dry run: 4 locators generated, nothing was written." in result.output
    assert "Tokens used: " in result.output
    assert not page_path("sample-page").parent.exists()


def test_dry_run_leaves_an_existing_page_as_it_is(project, site, fake_llm):
    url = f"{site}/sample_page.html"
    fake_llm(SAMPLE_LOCATORS)
    runner.invoke(app, ["locators", url])
    before = saved_file(project).read_bytes()
    fake_llm(SAMPLE_LOCATORS[:1])

    result = runner.invoke(app, ["locators", url, "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run: 1 locators generated" in result.output
    assert saved_file(project).read_bytes() == before


def test_private_page_without_session_stops_with_zero_llm_calls(
    project, site, fake_llm
):
    scripted = fake_llm(ACCOUNT_LOCATORS)

    result = runner.invoke(app, ["locators", f"{site}/account"])

    assert result.exit_code == 1
    assert "This page requires logging in." in result.output
    assert f"testwise auth {site}/account" in result.output
    assert scripted.requests == []
    assert not saved_file(project, "account").exists()


def test_auth_option_generates_locators_of_a_private_page(project, site, fake_llm):
    folder = project / ".testwise" / "auth"
    folder.mkdir(parents=True)
    (folder / "default.json").write_text(json.dumps(VALID_SESSION), encoding="utf-8")
    scripted = fake_llm(ACCOUNT_LOCATORS)

    result = runner.invoke(app, ["locators", f"{site}/account", "--auth", "default"])

    assert result.exit_code == 0, result.output
    assert "Welcome back" in scripted.first_prompt
    assert load_page("account").locator("welcome_heading").role == "heading"


def test_unknown_session_is_reported(project, site, fake_llm):
    scripted = fake_llm(ACCOUNT_LOCATORS)

    result = runner.invoke(app, ["locators", f"{site}/account", "--auth", "admin"])

    assert result.exit_code == 1
    assert "No saved session named 'admin'" in result.output
    assert scripted.requests == []


def test_missing_configuration_stops_before_opening_the_browser(project, site):
    result = runner.invoke(app, ["locators", f"{site}/sample_page.html"])

    assert result.exit_code == 1
    assert "Incomplete configuration" in result.output
    assert "Missing TESTWISE_MODEL" in result.output
    assert "Exploring" not in result.output


def test_llm_failure_is_reported_with_the_tokens_spent(project, site, fake_llm):
    scripted = fake_llm([{"name": "not valid"}])

    result = runner.invoke(app, ["locators", f"{site}/sample_page.html"])

    assert result.exit_code == 1
    assert "did not return valid locators after 2 retries" in result.output
    assert "Tokens used: " in result.output
    assert "in 3 request(s)" in result.output
    assert "Traceback" not in result.output
    assert len(scripted.requests) == 3
    assert not saved_file(project).exists()


def test_page_without_locators_is_not_saved(project, site, fake_llm):
    fake_llm([])

    result = runner.invoke(app, ["locators", f"{site}/landing"])

    assert result.exit_code == 1
    assert "The model found no elements" in result.output
    assert not saved_file(project, "landing").exists()


def test_unreachable_page_is_reported(project, fake_llm):
    scripted = fake_llm(SAMPLE_LOCATORS)

    result = runner.invoke(app, ["locators", "not-a-url"])

    assert result.exit_code == 1
    assert "Invalid URL" in result.output
    assert scripted.requests == []


def test_llm_error_is_an_exception_of_the_llm_layer():
    # The command relies on this to tell LLM failures from browser failures.
    assert not issubclass(LLMError, LoginRequiredError)


def test_paths_are_shown_relative_to_the_project(project, site, fake_llm):
    fake_llm(SAMPLE_LOCATORS)
    url = f"{site}/sample_page.html"

    first = runner.invoke(app, ["locators", url])
    second = runner.invoke(app, ["locators", url])

    shown = ".testwise/pages/sample-page.page.json"
    assert f"Saved 4 locators to {shown}" in first.output
    assert f"already exists: {shown}" in second.output
