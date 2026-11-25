import os

import pytest
from typer.testing import CliRunner

from testwise.cli import app
from testwise.settings import Settings, SettingsError, get_settings

SECRET = "sk-ant-super-secret-123"
runner = CliRunner()


def write_env(project, *lines: str) -> None:
    (project / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_loads_model_and_key_from_env_file(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    settings = Settings()

    assert settings.testwise_model == "anthropic:claude-sonnet-5-5"
    assert settings.anthropic_api_key.get_secret_value() == SECRET
    assert settings.testwise_cheap_model is None
    assert settings.openai_api_key is None


def test_only_the_key_of_the_chosen_provider_is_required(project):
    write_env(project, "TESTWISE_MODEL=openai:gpt-5", "OPENAI_API_KEY=sk-openai")

    assert get_settings().openai_api_key.get_secret_value() == "sk-openai"


def test_other_variables_in_the_env_file_are_ignored(project):
    write_env(
        project,
        "DATABASE_URL=postgres://localhost/app",
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    assert get_settings().testwise_model == "anthropic:claude-sonnet-5-5"


def test_environment_variables_win_over_env_file(project, monkeypatch):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )
    monkeypatch.setenv("TESTWISE_MODEL", "anthropic:claude-haiku-5-5")

    assert get_settings().testwise_model == "anthropic:claude-haiku-5-5"


def test_cheap_model_is_loaded_when_present(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        "TESTWISE_CHEAP_MODEL=anthropic:claude-haiku-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    assert get_settings().testwise_cheap_model == "anthropic:claude-haiku-5-5"


def test_get_settings_exports_the_key_for_pydantic_ai(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    get_settings()

    assert os.environ["ANTHROPIC_API_KEY"] == SECRET


def test_missing_model_is_explained(project):
    with pytest.raises(SettingsError, match="Missing TESTWISE_MODEL"):
        get_settings()


def test_missing_key_names_the_variable(project):
    write_env(project, "TESTWISE_MODEL=anthropic:claude-sonnet-5-5")

    with pytest.raises(SettingsError, match="Missing ANTHROPIC_API_KEY"):
        get_settings()


def test_empty_key_counts_as_missing(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        "ANTHROPIC_API_KEY=",
    )

    with pytest.raises(SettingsError, match="Missing ANTHROPIC_API_KEY"):
        get_settings()


def test_cheap_model_from_another_provider_needs_its_own_key(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        "TESTWISE_CHEAP_MODEL=google:gemini-flash",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    with pytest.raises(SettingsError, match="Missing GEMINI_API_KEY"):
        get_settings()


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "anthropic:", ":claude"])
def test_model_must_be_provider_colon_model(project, model):
    write_env(project, f"TESTWISE_MODEL={model}", f"ANTHROPIC_API_KEY={SECRET}")

    with pytest.raises(SettingsError, match="provider:model format"):
        get_settings()


def test_provider_without_known_key_is_not_validated(project):
    write_env(project, "TESTWISE_MODEL=ollama:llama3")

    assert get_settings().testwise_model == "ollama:llama3"


def test_key_never_appears_in_errors_or_repr(project):
    write_env(project, "TESTWISE_MODEL=no-format", f"ANTHROPIC_API_KEY={SECRET}")
    with pytest.raises(SettingsError) as error:
        get_settings()
    assert SECRET not in str(error.value)

    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )
    assert SECRET not in repr(get_settings())


def test_check_command_fails_with_clear_message_when_key_is_missing(project):
    write_env(project, "TESTWISE_MODEL=anthropic:claude-sonnet-5-5")

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 1
    assert "Missing ANTHROPIC_API_KEY" in result.output
    assert "Traceback" not in result.output


def test_check_command_passes_without_printing_the_key(project):
    write_env(
        project,
        "TESTWISE_MODEL=anthropic:claude-sonnet-5-5",
        f"ANTHROPIC_API_KEY={SECRET}",
    )

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0
    assert "anthropic:claude-sonnet-5-5" in result.output
    assert SECRET not in result.output
