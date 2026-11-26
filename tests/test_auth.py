import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from testwise import cli
from testwise.cli import app
from testwise.explorer import ExploreError, explore
from testwise.explorer.auth import AuthError, load_session, save_session, session_path

runner = CliRunner()


def log_in(page) -> None:
    """What the user does by hand in the browser window."""
    page.get_by_role("button", name="Log in").click()


def do_nothing(page) -> None:
    pass


def save(site: str, name: str = "default", wait_for_login=log_in):
    """save_session without a visible window, so it also runs in CI."""
    return save_session(
        f"{site}/login", name, wait_for_login=wait_for_login, headless=True
    )


@pytest.fixture
def headless_auth(monkeypatch):
    """Make `testwise auth` run headless and click "Log in" before Enter."""

    def fake_save_session(url, name, *, wait_for_login, timeout):
        def log_in_then_wait(page):
            log_in(page)
            wait_for_login(page)

        return save_session(
            url, name, wait_for_login=log_in_then_wait, timeout=timeout, headless=True
        )

    monkeypatch.setattr(cli, "save_session", fake_save_session)


# --- Saving and reusing a session -------------------------------------------


def test_session_is_saved_in_the_project(project, site):
    path = save(site)

    assert path == project / ".testwise" / "auth" / "default.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert [cookie["name"] for cookie in state["cookies"]] == ["session"]
    assert state["origins"][0]["localStorage"] == [{"name": "token", "value": "t-1"}]


def test_private_page_needs_the_session(project, site):
    with pytest.raises(ExploreError, match="HTTP 401"):
        explore(f"{site}/private")


def test_saved_session_opens_a_private_page(project, site):
    save(site)

    snapshot = explore(f"{site}/private", storage_state=load_session("default"))

    assert snapshot.title == "Account"
    assert "Welcome back" in snapshot.aria_snapshot


def test_sessions_with_different_names_are_kept_apart(project, site):
    admin = save(site, "admin")
    customer = save(site, "customer", wait_for_login=do_nothing)

    assert admin != customer
    assert explore(f"{site}/private", storage_state=load_session("admin"))
    with pytest.raises(ExploreError, match="HTTP 401"):
        explore(f"{site}/private", storage_state=load_session("customer"))


def test_saving_again_replaces_the_session(project, site):
    save(site, wait_for_login=do_nothing)
    path = save(site)

    assert json.loads(path.read_text(encoding="utf-8"))["cookies"]


def test_login_page_that_answers_with_an_error_status_is_still_opened(project, site):
    path = save_session(f"{site}/private", wait_for_login=do_nothing, headless=True)

    assert path.is_file()


def test_closing_the_browser_before_saving_is_explained(project, site):
    def close_browser(page) -> None:
        page.context.browser.close()

    with pytest.raises(AuthError, match="browser was closed before"):
        save(site, wait_for_login=close_browser)
    assert not session_path("default").exists()


def test_unreachable_login_page_is_reported(project):
    with pytest.raises(ExploreError, match="Invalid URL"):
        save_session("not-a-url", wait_for_login=do_nothing, headless=True)


# --- Names and paths --------------------------------------------------------


@pytest.mark.parametrize("name", ["../secrets", "a/b", "a\\b", "", ".hidden", "a b"])
def test_session_name_cannot_escape_the_auth_folder(project, name):
    with pytest.raises(AuthError, match="Invalid session name"):
        session_path(name)


def test_loading_a_session_that_does_not_exist_tells_how_to_create_it(project):
    with pytest.raises(AuthError, match="testwise auth <url> --name admin"):
        load_session("admin")


# --- Git --------------------------------------------------------------------


def test_auth_folder_is_added_to_gitignore(project, site):
    (project / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    save(site)

    lines = (project / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "node_modules" in lines
    assert ".testwise/auth/" in lines


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_ignores_the_session_file(project, site):
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    path = save(site)

    relative = path.relative_to(project).as_posix()
    ignored = subprocess.run(["git", "check-ignore", "-q", relative], cwd=project)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert ignored.returncode == 0
    assert relative not in status.stdout


# --- Commands ---------------------------------------------------------------


def test_auth_command_saves_the_session_and_warns_about_cookies(
    project, site, headless_auth
):
    result = runner.invoke(app, ["auth", f"{site}/login"], input="\n")

    assert result.exit_code == 0
    assert (project / ".testwise" / "auth" / "default.json").is_file()
    assert "Press Enter here when you are logged in" in result.output
    assert "Session 'default' saved" in result.output
    assert "contains your session cookies" in result.output
    assert "abc123" not in result.output


def test_auth_then_snapshot_with_auth_is_logged_in(project, site, headless_auth):
    runner.invoke(app, ["auth", f"{site}/login"], input="\n")

    result = runner.invoke(app, ["snapshot", f"{site}/private", "--auth", "default"])

    assert result.exit_code == 0
    assert "Title: Account" in result.output
    assert "Welcome back" in result.output


def test_auth_command_accepts_a_session_name(project, site, headless_auth):
    result = runner.invoke(
        app, ["auth", f"{site}/login", "--name", "admin"], input="\n"
    )

    assert result.exit_code == 0
    assert (project / ".testwise" / "auth" / "admin.json").is_file()
    assert "--auth admin" in result.output


def test_auth_command_reports_errors_without_a_traceback(project, headless_auth):
    result = runner.invoke(app, ["auth", "not-a-url"], input="\n")

    assert result.exit_code == 1
    assert "Invalid URL" in result.output
    assert "Traceback" not in result.output


def test_snapshot_with_unknown_session_tells_how_to_create_it(project, site):
    result = runner.invoke(app, ["snapshot", f"{site}/private", "--auth", "admin"])

    assert result.exit_code == 1
    assert "No saved session named 'admin'" in result.output
    assert "testwise auth <url> --name admin" in result.output


def test_snapshot_rejects_auth_together_with_storage_state(project, site, tmp_path):
    state = tmp_path / "state.json"
    state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["snapshot", f"{site}/whoami", "--auth", "x", "--storage-state", str(state)],
    )

    assert result.exit_code == 1
    assert "not both" in result.output
