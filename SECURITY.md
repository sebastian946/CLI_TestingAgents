# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use
**Security > Report a vulnerability** on the GitHub repository instead, so the
report stays private until it is fixed.

## What Test Wise does with your data

- **API keys.** Test Wise is bring-your-own-key. Your key is read from the
  `.env` file or the environment of your machine and is sent only to the
  provider you chose (Anthropic, OpenAI or Google). It is never logged or
  written to any other file.
- **Page content.** `testwise locators` sends a reduced accessibility snapshot
  of the page (roles, names and short visible texts) to your model provider.
  Do not run it on pages whose content you are not allowed to share with that
  provider.
- **Sessions.** `testwise auth` saves cookies and local storage to
  `.testwise/auth/<name>.json`. Anyone who has that file can act as you on that
  site. It stays on your machine and is ignored by git.

## Files you must never commit or share

- `.env`
- `.testwise/auth/`

Both are added to `.gitignore` by `testwise init` and `testwise auth`. If one
of them is ever exposed, revoke the API key at your provider and log out of the
affected site to invalidate the session.

## Responsible use

Run Test Wise only against sites you own or are authorized to test.
