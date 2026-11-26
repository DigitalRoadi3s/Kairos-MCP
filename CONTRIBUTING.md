# Contributing to Kairos-MCP

Thanks for helping. Bug reports, fixes, provider plugins and docs are all
welcome.

## Reporting a bug

Open an issue with what you ran, what you expected and what happened. Include
the provider (iCloud, Nextcloud, Google and so on) and the Kairos version.
Remove passwords, tokens, calendar URLs and event details from logs first.

For a security problem, don't open an issue; see [SECURITY.md](SECURITY.md).

## Setting up

Python 3.12 or newer.

```bash
git clone https://github.com/DigitalRoadi3s/Kairos-MCP.git
cd Kairos-MCP
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

CI runs these on every pull request. Run them before you push:

```bash
ruff check . && ruff format --check . && mypy
pytest                       # includes integration tests against a real Radicale server
pytest -m "not integration"  # faster: unit and contract tests only
```

The integration tests start Radicale, a small CalDAV server, so they need no
real calendar account. Never commit real credentials or calendar data, even
in tests.

## Sending a change

1. Branch from `main`.
2. Keep each pull request to one change, with tests that cover it.
3. Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md)
   under Added, Changed, Removed or Fixed. Mark a change that breaks existing
   configs or clients as **Breaking**.
4. Update the README or `guides/` when behaviour or configuration changes.
5. Open the pull request against `main` and describe what a user would see
   before and after.

A calendar service that needs its own code usually fits better as a separate
plugin package than as a change to Kairos. See
[guides/providers.md](guides/providers.md).

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
