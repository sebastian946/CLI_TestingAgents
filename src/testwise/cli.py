from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from .core import greetings
from .explorer import (
    DEFAULT_SESSION,
    DEFAULT_TIMEOUT,
    ExploreError,
    explore,
    filter_with_stats,
    load_session,
    save_session,
)
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
    auth_name: Annotated[
        str | None,
        typer.Option(
            "--auth",
            metavar="NAME",
            help="Explore with a session saved by `testwise auth` (e.g. default).",
        ),
    ] = None,
    ignore_http_errors: Annotated[
        bool,
        typer.Option(
            "--ignore-http-errors",
            help="Snapshot the page even if the server answers 4xx or 5xx.",
        ),
    ] = False,
    filtered: Annotated[
        bool,
        typer.Option(
            "--filter",
            help="Print the snapshot reduced to what the LLM needs, with its size.",
        ),
    ] = False,
) -> None:
    """Debug: open URL in headless Chromium and print its accessibility snapshot."""
    if auth_name and storage_state:
        typer.secho("Use --auth or --storage-state, not both.", fg="red", err=True)
        raise typer.Exit(code=1)

    try:
        if auth_name:
            storage_state = load_session(auth_name)
        page_snapshot = explore(
            url,
            storage_state=storage_state,
            timeout=timeout,
            check_status=not ignore_http_errors,
        )
    except ExploreError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None

    captured_at = page_snapshot.captured_at.isoformat(timespec="seconds")
    typer.echo(f"URL: {page_snapshot.url}")
    typer.echo(f"Title: {page_snapshot.title}")
    typer.echo(f"Captured at: {captured_at}")
    typer.echo()
    if filtered:
        text, stats = filter_with_stats(page_snapshot.aria_snapshot)
        typer.echo(text)
        typer.secho(f"\nFiltered: {stats}", fg="green", err=True)
    else:
        typer.echo(page_snapshot.aria_snapshot)


@app.command()
def auth(
    url: str,
    name: Annotated[
        str,
        typer.Option(help="Name of the session, to keep several (admin, customer)."),
    ] = DEFAULT_SESSION,
    timeout: Annotated[
        float, typer.Option(min=0.1, help="Seconds to wait for the page to open.")
    ] = DEFAULT_TIMEOUT,
) -> None:
    """Log in by hand in a visible browser and save the session for later use."""

    def wait_for_login(_page) -> None:
        typer.echo("Log in in the browser window, including any 2FA step.")
        typer.prompt(
            "Press Enter here when you are logged in",
            default="",
            show_default=False,
            prompt_suffix=" ",
        )

    typer.echo(f"Opening {url} in a browser window...")
    try:
        path = save_session(url, name, wait_for_login=wait_for_login, timeout=timeout)
    except ExploreError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None

    typer.secho(f"Session '{name}' saved to {path}", fg="green")
    typer.secho(
        "Warning: this file contains your session cookies. Anyone who has it can "
        "act as you on that site. It stays on this machine and is ignored by git: "
        "do not share it or commit it.",
        fg="yellow",
    )
    typer.echo(f"Use it with: testwise snapshot <url> --auth {name}")


if __name__ == "__main__":
    app()
