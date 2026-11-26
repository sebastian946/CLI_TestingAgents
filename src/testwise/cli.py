from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from .core import greetings
from .explorer import (
    DEFAULT_SESSION,
    DEFAULT_TIMEOUT,
    ExploreError,
    LoginRequiredError,
    explore,
    filter_with_stats,
    load_session,
    save_session,
)
from .locators.models import Locator
from .scaffold import InitError, init_project
from .settings import PROVIDER_KEYS, Settings, SettingsError, get_settings
from .storage import PageFileError, page_path, save_page, slug_from_url

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


def _login_required_message(error: LoginRequiredError, auth_name: str | None) -> str:
    """Tell the user which command fixes a page that needs a login."""
    if not error.has_session:
        return (
            f"This page requires logging in. Run: testwise auth {error.url}\n"
            f"Then repeat this command with --auth {DEFAULT_SESSION}."
        )
    if auth_name is None:
        return str(error)
    name_option = "" if auth_name == DEFAULT_SESSION else f" --name {auth_name}"
    return (
        f"The saved session '{auth_name}' has expired or is not valid for this "
        f"page. Log in again with: testwise auth {error.url}{name_option}"
    )


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
    skip_login_check: Annotated[
        bool,
        typer.Option(
            "--skip-login-check",
            help="Snapshot the page even if it looks like a login screen.",
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
            check_login=not skip_login_check,
        )
    except LoginRequiredError as error:
        typer.secho(_login_required_message(error, auth_name), fg="red", err=True)
        typer.echo("If that is not a login screen, add --skip-login-check.", err=True)
        raise typer.Exit(code=1) from None
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


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Plain text table with one line per row and aligned columns."""
    columns = zip(headers, *rows, strict=True)
    widths = [max(len(cell) for cell in column) for column in columns]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) for cell, width in zip(cells, widths, strict=True)
        ).rstrip()

    separator = ["-" * width for width in widths]
    return "\n".join(line(cells) for cells in [headers, separator, *rows])


def _shown(path: Path) -> str:
    """The path as the user would type it from the current folder."""
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return str(path)


def _locator_target(locator: Locator) -> str:
    """What the locator looks for, as short text for the table."""
    if locator.role is None:
        return locator.value or ""
    return f'{locator.role} "{locator.value}"' if locator.value else locator.role


@app.command()
def locators(
    url: str,
    name: Annotated[
        str | None,
        typer.Option(help="Name of the page. By default it is taken from the URL."),
    ] = None,
    auth_name: Annotated[
        str | None,
        typer.Option(
            "--auth",
            metavar="NAME",
            help="Explore with a session saved by `testwise auth` (e.g. default).",
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace the page.json if it exists.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Run everything but do not write the file."),
    ] = False,
    timeout: Annotated[
        float, typer.Option(min=0.1, help="Seconds to wait for the page.")
    ] = DEFAULT_TIMEOUT,
    ignore_http_errors: Annotated[
        bool,
        typer.Option(
            "--ignore-http-errors",
            help="Use the page even if the server answers 4xx or 5xx.",
        ),
    ] = False,
    skip_login_check: Annotated[
        bool,
        typer.Option(
            "--skip-login-check",
            help="Use the page even if it looks like a login screen.",
        ),
    ] = False,
) -> None:
    """Generate the locators of a page with the LLM and save them to a page.json."""
    load_settings_or_exit()

    try:
        page_name = name or slug_from_url(url)
        path = page_path(page_name)
    except PageFileError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None

    # Checked first: an existing page must not cost a browser run or any token.
    if path.exists() and not force and not dry_run:
        typer.secho(
            f"The page '{page_name}' already exists: {_shown(path)}", fg="red", err=True
        )
        typer.echo(
            "Use --check to verify its locators without the LLM, or --force to "
            "generate them again.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Imported here because the LLM library is slow to load, and the other
    # commands do not need it.
    from . import pipeline
    from .llm import LLMError

    typer.echo(f"Exploring {url} ...")
    try:
        storage_state = load_session(auth_name) if auth_name else None
        generated = pipeline.generate_page(
            url,
            name=page_name,
            storage_state=storage_state,
            timeout=timeout,
            check_status=not ignore_http_errors,
            check_login=not skip_login_check,
        )
    except LoginRequiredError as error:
        typer.secho(_login_required_message(error, auth_name), fg="red", err=True)
        typer.echo("If that is not a login screen, add --skip-login-check.", err=True)
        raise typer.Exit(code=1) from None
    except ExploreError as error:
        typer.secho(str(error), fg="red", err=True)
        raise typer.Exit(code=1) from None
    except LLMError as error:
        typer.secho(str(error), fg="red", err=True)
        if error.usage.requests:
            typer.echo(f"Tokens used: {error.usage}", err=True)
        raise typer.Exit(code=1) from None

    page = generated.page
    if not page.locators:
        typer.secho(
            "The model found no elements on this page. Nothing was saved.",
            fg="red",
            err=True,
        )
        typer.echo(f"Tokens used: {generated.usage} ({generated.model})", err=True)
        raise typer.Exit(code=1)

    rows = [
        [item.name, item.strategy.value, _locator_target(item), item.status.value]
        for item in page.locators
    ]
    typer.echo()
    typer.echo(_table(["Name", "Strategy", "Target", "Status"], rows))
    typer.echo()

    if dry_run:
        typer.secho(
            f"Dry run: {len(page.locators)} locators generated, nothing was written.",
            fg="yellow",
        )
    else:
        saved = save_page(page)
        typer.secho(
            f"Saved {len(page.locators)} locators to {_shown(saved)}", fg="green"
        )
    typer.echo(f"Snapshot: {generated.snapshot_stats}")
    typer.echo(f"Tokens used: {generated.usage} ({generated.model})")


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
