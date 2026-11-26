import logging
from pathlib import Path

import pytest
from fakes import ScriptedModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from testwise.explorer import filter_snapshot
from testwise.llm import (
    MAX_OUTPUT_RETRIES,
    SYSTEM_PROMPT,
    LLMError,
    TokenUsage,
    generate_locators,
    structure_output,
)
from testwise.locators import Locator, LocatorStatus, PageContext, Strategy
from testwise.settings import SettingsError

SNAPSHOTS = Path(__file__).parent / "fixtures" / "snapshots"
LOGIN_SNAPSHOT = filter_snapshot((SNAPSHOTS / "login.yaml").read_text(encoding="utf-8"))
LOGIN_LOCATORS = [
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
        "value": "Login",
    },
]


# --- Valid answers ----------------------------------------------------------


def test_returns_a_valid_list_of_locators_with_the_test_model():
    model = TestModel(custom_output_args=LOGIN_LOCATORS)

    result = generate_locators(LOGIN_SNAPSHOT, model=model)

    assert [locator.name for locator in result.locators] == [
        "username_input",
        "password_input",
        "login_button",
    ]
    assert all(isinstance(locator, Locator) for locator in result.locators)
    assert result.locators[2].strategy is Strategy.ROLE
    assert result.model == "test"


def test_locators_fit_in_a_page_context():
    result = generate_locators(
        LOGIN_SNAPSHOT, model=TestModel(custom_output_args=LOGIN_LOCATORS)
    )

    page = PageContext(
        name="login",
        url="https://www.saucedemo.com/",
        captured_at="2026-11-24T15:00:00Z",
        locators=result.locators,
    )

    assert page.locator("login_button").value == "Login"


def test_locators_from_the_model_are_never_marked_as_verified():
    claimed = [
        LOGIN_LOCATORS[2] | {"status": "ok", "last_verified": "2026-11-24T15:00:00Z"}
    ]

    result = generate_locators(
        LOGIN_SNAPSHOT, model=TestModel(custom_output_args=claimed)
    )

    assert result.locators[0].status is LocatorStatus.UNVERIFIED
    assert result.locators[0].last_verified is None


# --- What is sent to the model ----------------------------------------------


def test_system_prompt_has_the_rules_of_the_ticket():
    order = ["test_id", "role", "label", "placeholder", "text", "css"]
    positions = [SYSTEM_PROMPT.index(f"{strategy}:") for strategy in order]

    assert positions == sorted(positions)
    assert "snake_case" in SYSTEM_PROMPT
    assert "login_button, not button_1" in SYSTEM_PROMPT
    assert "Never invent elements" in SYSTEM_PROMPT
    assert "xpath" not in SYSTEM_PROMPT


def test_system_prompt_is_identical_between_calls_and_pages():
    first = ScriptedModel(LOGIN_LOCATORS)
    second = ScriptedModel([])

    generate_locators(LOGIN_SNAPSHOT, url="https://a.test/login", model=first.model)
    generate_locators('- heading "Other page"', model=second.model)

    sent = [first.requests[0][1].instructions, second.requests[0][1].instructions]
    assert sent == [SYSTEM_PROMPT.strip(), SYSTEM_PROMPT.strip()]


def test_page_data_goes_in_the_user_message_not_in_the_system_prompt():
    scripted = ScriptedModel(LOGIN_LOCATORS)

    generate_locators(LOGIN_SNAPSHOT, url="https://a.test/login", model=scripted.model)

    messages, info = scripted.requests[0]
    user_message = str(messages[0].parts[0].content)
    assert LOGIN_SNAPSHOT in user_message
    assert "Page URL: https://a.test/login" in user_message
    assert "Username" not in info.instructions


def test_answer_must_be_structured_not_free_text():
    scripted = ScriptedModel(LOGIN_LOCATORS)

    generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    _, info = scripted.requests[0]
    assert not info.allow_text_output
    assert len(info.output_tools) == 1


# --- Retries ----------------------------------------------------------------


def test_malformed_answer_is_retried_and_then_accepted():
    malformed = [{"name": "Login Button", "strategy": "xpath", "value": "//button"}]
    scripted = ScriptedModel(malformed, LOGIN_LOCATORS)

    result = generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    assert len(scripted.requests) == 2
    assert len(result.locators) == 3
    assert result.usage.requests == 2
    # The model is told what was wrong so that it can fix it.
    assert "xpath" in scripted.last_feedback


def test_answer_that_stays_malformed_fails_with_a_clear_error():
    scripted = ScriptedModel([{"name": "x"}])

    with pytest.raises(LLMError) as error:
        generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    # One request plus the retries, and no more.
    assert len(scripted.requests) == 1 + MAX_OUTPUT_RETRIES == 3
    message = str(error.value)
    assert "did not return valid locators after 2 retries" in message
    assert "TESTWISE_MODEL" in message
    assert "Traceback" not in message
    assert "pydantic" not in message.lower()


def test_free_text_instead_of_locators_is_retried_then_fails():
    scripted = ScriptedModel("Sure! Here are your locators: ...")

    with pytest.raises(LLMError, match="did not return valid locators"):
        generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    assert len(scripted.requests) == 3


def test_duplicated_names_are_sent_back_to_the_model():
    duplicated = [LOGIN_LOCATORS[0], LOGIN_LOCATORS[1] | {"name": "username_input"}]
    scripted = ScriptedModel(duplicated, LOGIN_LOCATORS)

    result = generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    assert len(scripted.requests) == 2
    assert "More than one locator is named 'username_input'" in scripted.last_feedback
    assert len({locator.name for locator in result.locators}) == 3


def test_invented_elements_are_sent_back_to_the_model():
    invented = LOGIN_LOCATORS + [
        {
            "name": "forgot_password_link",
            "strategy": "role",
            "role": "link",
            "value": "Forgot your password?",
        }
    ]
    scripted = ScriptedModel(invented, LOGIN_LOCATORS)

    result = generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    assert len(scripted.requests) == 2
    assert "'forgot_password_link'" in scripted.last_feedback
    assert "does not appear in the snapshot" in scripted.last_feedback
    assert "forgot_password_link" not in [locator.name for locator in result.locators]


def test_text_is_compared_without_the_escaping_of_the_snapshot():
    snapshot = '- button "Save \\"draft\\"  now"'
    locator = {
        "name": "save_button",
        "strategy": "role",
        "role": "button",
        "value": 'Save "draft" now',
    }

    result = generate_locators(snapshot, model=ScriptedModel([locator]).model)

    assert result.locators[0].value == 'Save "draft" now'


def test_css_and_test_id_values_are_not_looked_up_in_the_snapshot():
    locators = [
        {"name": "cart_icon", "strategy": "css", "value": "a.shopping_cart_link"},
        {"name": "menu_button", "strategy": "test_id", "value": "open-menu"},
    ]
    scripted = ScriptedModel(locators)

    result = generate_locators(LOGIN_SNAPSHOT, model=scripted.model)

    assert len(scripted.requests) == 1
    assert len(result.locators) == 2


def test_page_without_elements_gives_an_empty_list():
    result = generate_locators("- text: Nothing here", model=ScriptedModel([]).model)

    assert result.locators == []


# --- Token usage ------------------------------------------------------------


def test_token_usage_is_returned_for_the_report():
    result = generate_locators(
        LOGIN_SNAPSHOT, model=TestModel(custom_output_args=LOGIN_LOCATORS)
    )

    usage = result.usage
    assert isinstance(usage, TokenUsage)
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert usage.requests == 1
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens


def test_usage_adds_up_the_retries():
    once = generate_locators(LOGIN_SNAPSHOT, model=ScriptedModel(LOGIN_LOCATORS).model)
    twice = generate_locators(
        LOGIN_SNAPSHOT, model=ScriptedModel([{"name": "x"}], LOGIN_LOCATORS).model
    )

    assert twice.usage.requests == 2
    assert twice.usage.input_tokens > once.usage.input_tokens


def test_usage_of_every_call_is_logged(caplog):
    with caplog.at_level(logging.INFO, logger="testwise.llm.structure_output"):
        result = generate_locators(
            LOGIN_SNAPSHOT, model=TestModel(custom_output_args=LOGIN_LOCATORS)
        )

    assert f"LLM call to test: {result.usage}" in caplog.text
    assert "input + " in caplog.text


def test_tokens_spent_on_a_failed_call_are_reported_too(caplog):
    with caplog.at_level(logging.INFO, logger="testwise.llm.structure_output"):
        with pytest.raises(LLMError) as error:
            generate_locators(
                LOGIN_SNAPSHOT, model=ScriptedModel([{"name": "x"}]).model
            )

    assert error.value.usage.requests == 3
    assert error.value.usage.input_tokens > 0
    assert "in 3 request(s)" in caplog.text


def test_usage_reads_well():
    usage = TokenUsage(input_tokens=1200, output_tokens=340, requests=2)

    assert str(usage) == "1,200 input + 340 output tokens in 2 request(s)"
    assert usage.total_tokens == 1540


# --- Errors and configuration -----------------------------------------------


def test_empty_snapshot_fails_without_calling_the_model():
    scripted = ScriptedModel(LOGIN_LOCATORS)

    with pytest.raises(LLMError, match="snapshot is empty"):
        generate_locators("  \n", model=scripted.model)

    assert scripted.requests == []


def test_provider_error_is_reported_without_a_traceback():
    def rejected(messages, info):
        raise ModelHTTPError(401, "claude-test", {"error": "invalid x-api-key"})

    with pytest.raises(LLMError) as error:
        generate_locators(LOGIN_SNAPSHOT, model=FunctionModel(rejected))

    message = str(error.value)
    assert message.startswith("The request to function:rejected: failed: ")
    assert "401" in message
    assert "\n" not in message


def test_missing_configuration_fails_before_any_request(project):
    with pytest.raises(SettingsError, match="Missing TESTWISE_MODEL"):
        generate_locators(LOGIN_SNAPSHOT)


def test_model_comes_from_the_byok_configuration(project, monkeypatch):
    (project / ".env").write_text(
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5\nANTHROPIC_API_KEY=sk-ant-fake\n",
        encoding="utf-8",
    )
    used = []
    build_agent = structure_output._build_agent

    def build_with_a_fake_model(model):
        used.append(model)
        return build_agent(TestModel(custom_output_args=LOGIN_LOCATORS))

    monkeypatch.setattr(structure_output, "_build_agent", build_with_a_fake_model)

    result = generate_locators(LOGIN_SNAPSHOT)

    assert used == ["anthropic:claude-sonnet-5-5"]
    assert result.model == "anthropic:claude-sonnet-5-5"
