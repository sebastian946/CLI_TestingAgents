# Test Wise CLI

[![CI](https://github.com/sebastian946/CLI_TestingAgents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sebastian946/CLI_TestingAgents/actions/workflows/ci.yml)

## Setup

Install the dependencies:

```
uv sync
```

Install the browser that Test Wise uses to explore pages. This is a one-time
download and is required by `testwise snapshot` and by the tests:

```
uv run playwright install chromium
```

## Usage

### Generate the locators of a page

Set your model and API key first: `uv run testwise init` creates a
`.env.example` to copy to `.env`, and `uv run testwise check` validates it.

```
uv run testwise locators https://www.saucedemo.com
```

This opens the page, reduces its accessibility snapshot, asks your model for
the locators in a single call and saves them to
`.testwise/pages/<name>.page.json`. It ends with a table of the locators and the
tokens used.

| Option | What it does |
| --- | --- |
| `--name checkout` | Name of the page. By default it comes from the URL. |
| `--auth default` | Use a session saved by `testwise auth`. |
| `--dry-run` | Run everything but do not write the file. |
| `--force` | Replace a page.json that already exists. |

Without `--force`, an existing page.json is never touched and the command stops
before opening the browser or calling the model.

### Inspect a page

Print the accessibility snapshot of a page (debugging command):

```
uv run testwise snapshot https://www.saucedemo.com
uv run testwise snapshot https://www.saucedemo.com --timeout 60
uv run testwise snapshot https://example.com/account --storage-state state.json
```

Add `--filter` to print the snapshot reduced to what the LLM needs (interactive
elements, headings, regions and short texts) together with its size before and
after:

```
uv run testwise snapshot https://books.toscrape.com --filter
```

### Pages behind a login

Log in by hand once and Test Wise reuses the session. A browser window opens;
log in there (including any 2FA step) and then press Enter in the terminal:

```
uv run testwise auth https://www.saucedemo.com
uv run testwise auth https://example.com/login --name admin
```

The session is saved to `.testwise/auth/<name>.json` (`default` if you do not
pass `--name`). Use it with `--auth`:

```
uv run testwise snapshot https://www.saucedemo.com/inventory.html --auth default --ignore-http-errors
uv run testwise snapshot https://example.com/admin --auth admin
```

The session file contains your session cookies: anyone who has it can act as you
on that site. It never leaves your machine, your password is never stored, and
`testwise auth` adds `.testwise/auth/` to `.gitignore`. Do not share or commit it.

If you open a private page without a session, or with one that has expired,
Test Wise notices that the site sent you to its login screen and stops there
with the `testwise auth` command to run, instead of working on the login screen
by mistake. Asking for the login page itself works as usual. If a page is wrongly
taken for a login screen, add `--skip-login-check`.

`--ignore-http-errors` is for single-page apps such as saucedemo, whose server
answers 404 on inner routes even though the page renders.

## Commands

uv run ruff check .

uv run ruff check --fix .

uv run ruff format .

uv run pytest
