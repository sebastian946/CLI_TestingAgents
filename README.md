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

`--ignore-http-errors` is for single-page apps such as saucedemo, whose server
answers 404 on inner routes even though the page renders.

## Commands

uv run ruff check .

uv run ruff check --fix .

uv run ruff format .

uv run pytest
