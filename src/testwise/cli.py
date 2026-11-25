from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from .core import greetings
from .explorer import DEFAULT_TIMEOUT, ExploreError, explore
from .scaffold import InitError, init_project
from .settings import PROVIDER_KEYS, Settings, SettingsError, get_settings

app = typer.Typer(help="CLI Test Wise.", no_args_is_help=True)


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"testwise, version {version('CLI_TestingAgents')}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    pass


def load_settings_or_exit() -> Settings:
    """Load the configuration; if something is missing, explain it and exit with 1.

    Every command that uses the LLM must get its configuration from this function.
    """
    try:
        return get_settings()
    except SettingsError as error:
        typer.secho(f"Incomplete configuration:\n{error}", fg="red", err=True)
        typer.echo("Run `testwise init` to generate a .env.example.", err=True)
        raise typer.Exit(code=1) from None


@app.command()
def greet(name: str) -> None:
    """Greet NAME."""
    typer.echo(greetings(name))


@app.command()
def init() -> None:
    """Set up the current project: .testwise/, .env.example and .gitignore."""
    try:
        steps = init_project(Path.cwd())
    except InitError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None

    for changed, message in steps:
        typer.secho(message, fg="green" if changed else None)
    typer.echo("Next step: copy .env.example to .env and fill in your values.")


@app.command()
def check() -> None:
    """Validate the configuration (model and API key) without calling the LLM."""
    settings = load_settings_or_exit()
    typer.echo(f"Main model: {settings.testwise_model}")
    if settings.testwise_cheap_model:
        typer.echo(f"Cheap model: {settings.testwise_cheap_model}")
    env_var = PROVIDER_KEYS.get(settings.testwise_model.partition(":")[0])
    if env_var:
        typer.echo(f"API key: {env_var} is set")
    typer.secho("Configuration is valid.", fg="green")


@app.command()
def snapshot(
    url: str,
    timeout: Annotated[
        float, typer.Option(min=0.1, help="Seconds to wait for the page.")
    ] = DEFAULT_TIMEOUT,
    storage_state: Annotated[
        Path | None,
        typer.Option(
            help="Playwright storage state file, to explore pages behind a login."
        ),
    ] = None,
) -> None:
    """Debug: open URL in headless Chromium and print its accessibility snapshot."""
    try:
        page_snapshot = explore(url, storage_state=storage_state, timeout=timeout)
    except ExploreError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None

    captured_at = page_snapshot.captured_at.isoformat(timespec="seconds")
    typer.echo(f"URL: {page_snapshot.url}")
    typer.echo(f"Title: {page_snapshot.title}")
    typer.echo(f"Captured at: {captured_at}")
    typer.echo()
    typer.echo(page_snapshot.aria_snapshot)


if __name__ == "__main__":
    app()
