"""BYOK configuration: users bring their own model and their own API key."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = ".env"
MODEL_EXAMPLE = "anthropic:claude-sonnet-5-5"

# PydanticAI provider -> environment variable where that provider looks for its key.
# Providers that are not listed here (ollama, groq, ...) are not validated:
# PydanticAI reads their own variables.
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-chat": "OPENAI_API_KEY",
    "openai-responses": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
}


class SettingsError(Exception):
    """The user's configuration is incomplete or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The .env belongs to the user's project: it may hold other variables.
        extra="ignore",
        # `ANTHROPIC_API_KEY=` (empty) counts as not set.
        env_ignore_empty=True,
        # Keeps pydantic from including values (the key) in its errors.
        hide_input_in_errors=True,
    )

    testwise_model: str
    testwise_cheap_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    @field_validator("testwise_model", "testwise_cheap_model")
    @classmethod
    def _check_model_format(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        provider, separator, name = value.strip().partition(":")
        if not separator or not provider or not name:
            raise ValueError(
                f"{info.field_name.upper()} must use the provider:model format "
                f"(for example {MODEL_EXAMPLE})."
            )
        return value.strip()

    @model_validator(mode="after")
    def _check_api_keys(self) -> "Settings":
        for model in (self.testwise_model, self.testwise_cheap_model):
            if model is None:
                continue
            env_var = PROVIDER_KEYS.get(model.partition(":")[0])
            if env_var and getattr(self, env_var.lower()) is None:
                raise ValueError(
                    f"Missing {env_var}: the model '{model}' requires it. "
                    f"Add it to your {ENV_FILE} or export it as an environment "
                    "variable."
                )
        return self


def get_settings() -> Settings:
    """Load the configuration from the environment and the .env in the current dir.

    Raises SettingsError with a user-facing message. The message never includes
    the value of the keys.
    """
    try:
        # Values come from the environment, not from arguments.
        settings = Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as error:
        raise SettingsError(_format_errors(error)) from None
    # PydanticAI reads keys from os.environ, not from the .env. Variables that are
    # already exported take precedence over the file.
    load_dotenv(Path.cwd() / ENV_FILE)
    return settings


def _format_errors(error: ValidationError) -> str:
    lines = []
    for item in error.errors(include_input=False, include_url=False):
        if item["type"] == "missing":
            name = str(item["loc"][0]).upper()
            lines.append(
                f"Missing {name}: set it in your {ENV_FILE} using the "
                f"provider:model format (for example {MODEL_EXAMPLE})."
            )
        else:
            lines.append(item["msg"].removeprefix("Value error, "))
    return "\n".join(lines)
