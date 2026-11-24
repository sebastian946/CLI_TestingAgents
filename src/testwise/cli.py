from importlib.metadata import version

import typer

from .core import greetings

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
        help="Muestra la versión y sale.",
    ),
) -> None:
    pass


@app.command()
def greet(name: str) -> None:
    """Saluda a NAME."""
    typer.echo(greetings(name))


if __name__ == "__main__":
    app()
