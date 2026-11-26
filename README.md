# Test Wise CLI

[![CI](https://github.com/sebastian946/CLI_TestingAgents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sebastian946/CLI_TestingAgents/actions/workflows/ci.yml)

Test Wise is an open source CLI that generates the Playwright locators of a web
page with a language model. You give it a URL and it saves a validated
`page.json`. You bring your own model and API key (Anthropic, OpenAI or
Gemini): there is no server in between.

## How it works

`testwise locators <url>` runs these steps:

1. **Checks.** Reads the model and API key from your `.env` and stops if the
   page already has a page.json.
2. **Explore.** Opens the page in headless Chromium with Playwright and takes
   its accessibility snapshot. It stops if the site sent you to a login screen.
3. **Filter.** Reduces the snapshot to buttons, links, form fields, headings
   and short texts. This is plain code, with no model involved.
4. **Ask the model.** Sends fixed instructions and the filtered snapshot to
   your model in a single call, and requires a structured answer.
5. **Validate.** Every locator must follow the schema, have a unique name and
   point to a text that exists in the snapshot. A wrong answer is sent back to
   the model to be fixed, up to two times.
6. **Save.** Writes `.testwise/pages/<name>.page.json` and prints the locators
   and the tokens used.

Steps 1 and 2 can stop the command before any token is spent.

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

## Security

- Your API key is read from `.env` and is sent only to the provider you chose.
- `testwise locators` sends the filtered snapshot of the page to that provider.
  Do not run it on pages whose content you may not share with it.
- Never commit or share `.env` or `.testwise/auth/`. Both are in `.gitignore`.
- Run Test Wise only against sites you own or are authorized to test.

See [SECURITY.md](SECURITY.md) for details and for how to report a
vulnerability.

## License

[MIT](LICENSE)

## Development

uv run ruff check .

uv run ruff check --fix .

uv run ruff format .

uv run pytest
