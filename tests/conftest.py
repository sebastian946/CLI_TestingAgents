import os

import pytest

CONFIG_VARS = (
    "TESTWISE_MODEL",
    "TESTWISE_CHEAP_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Empty project directory, with no testwise configuration in the environment."""
    saved = dict(os.environ)
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # get_settings() exports the .env to os.environ; restore it to isolate tests.
    os.environ.clear()
    os.environ.update(saved)
