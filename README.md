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

## Commands

uv run ruff check .

uv run ruff check --fix .

uv run ruff format .

uv run pytest
