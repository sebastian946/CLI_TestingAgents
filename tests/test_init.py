from typer.testing import CliRunner

from testwise.cli import app
from testwise.scaffold import ENV_EXAMPLE, init_project

runner = CliRunner()


def snapshot(project) -> dict[str, str]:
    return {
        path.relative_to(project).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def test_init_creates_folder_env_example_and_gitignore(project):
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (project / ".testwise").is_dir()
    assert (project / ".env.example").read_text(encoding="utf-8") == ENV_EXAMPLE
    gitignore = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    assert ".testwise/auth/" in gitignore


def test_init_is_idempotent(project):
    first = runner.invoke(app, ["init"])
    before = snapshot(project)

    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert snapshot(project) == before
    assert all(not changed for changed, _ in init_project(project))


def test_init_does_not_overwrite_existing_files(project):
    (project / ".testwise").mkdir()
    (project / ".testwise" / "notes.txt").write_text("mine", encoding="utf-8")
    (project / ".env.example").write_text("CUSTOM=1\n", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (project / ".env.example").read_text(encoding="utf-8") == "CUSTOM=1\n"
    notes = project / ".testwise" / "notes.txt"
    assert notes.read_text(encoding="utf-8") == "mine"


def test_init_appends_only_missing_gitignore_entries(project):
    (project / ".gitignore").write_text("node_modules\n.env", encoding="utf-8")

    runner.invoke(app, ["init"])

    lines = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines == ["node_modules", ".env", ".testwise/auth/"]


def test_init_never_creates_the_real_env_file(project):
    runner.invoke(app, ["init"])

    assert not (project / ".env").exists()


def test_init_fails_cleanly_when_testwise_is_a_file(project):
    (project / ".testwise").write_text("", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "is not a directory" in result.output
