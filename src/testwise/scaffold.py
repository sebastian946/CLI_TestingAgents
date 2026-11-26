"""Logic behind `testwise init`: sets up a project without overwriting anything."""

from pathlib import Path

TESTWISE_DIR = ".testwise"
ENV_EXAMPLE_FILE = ".env.example"
GITIGNORE_FILE = ".gitignore"
GITIGNORE_ENTRIES = (".env", ".testwise/auth/")

ENV_EXAMPLE = """\
# Test Wise configuration. Copy this file to .env and fill in the values.
# The .env file must never be committed to the repository.

# Main model, in provider:model format.
TESTWISE_MODEL=anthropic:claude-sonnet-5-5

# Optional: cheaper model used for repairs and summaries.
# TESTWISE_CHEAP_MODEL=anthropic:claude-haiku-5-5

# API key for the provider you use. You only need the one for your provider.
ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
# GEMINI_API_KEY=
"""


class InitError(Exception):
    """The project could not be set up."""


def init_project(root: Path) -> list[tuple[bool, str]]:
    """Set up `root` to use testwise.

    Returns a list of (changed, message), one per step. It is idempotent:
    whatever already exists is left as it is.
    """
    return [
        _ensure_dir(root / TESTWISE_DIR),
        _ensure_env_example(root / ENV_EXAMPLE_FILE),
        ensure_gitignore(root / GITIGNORE_FILE),
    ]


def _ensure_dir(folder: Path) -> tuple[bool, str]:
    if folder.is_dir():
        return False, f"{folder.name}/ already exists"
    if folder.exists():
        raise InitError(f"{folder.name} exists but is not a directory.")
    folder.mkdir()
    return True, f"{folder.name}/ created"


def _ensure_env_example(file: Path) -> tuple[bool, str]:
    if file.exists():
        return False, f"{file.name} already exists"
    file.write_text(ENV_EXAMPLE, encoding="utf-8")
    return True, f"{file.name} created"


def ensure_gitignore(file: Path) -> tuple[bool, str]:
    content = file.read_text(encoding="utf-8") if file.exists() else ""
    present = {line.strip() for line in content.splitlines()}
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in present]
    if not missing:
        return False, f"{file.name} already has the required entries"
    if content and not content.endswith("\n"):
        content += "\n"
    file.write_text(content + "\n".join(missing) + "\n", encoding="utf-8")
    return True, f"{file.name} updated ({', '.join(missing)})"
