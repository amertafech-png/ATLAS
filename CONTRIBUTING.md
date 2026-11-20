# Contributing to MODI

Thanks for helping! A few ground rules keep the project safe for everyone:

* **Every pull request is reviewed by the maintainer** before it is merged. Only the maintainer merges (`main` is protected).
* **Never put secrets in code, issues or PRs** - no API keys, tokens, passwords, personal data, or screenshots showing them. Hermes' API key lives in Hermes' own
  `.env`; MODI only reads it at run time. If you accidentally commit a secret, tell the maintainer privately (see [SECURITY.md](SECURITY.md)) and revoke it.
* Keep MODI **local-first**: no telemetry, no calls to servers the user didn't choose, and anything that acts on the user's computer must ask first
  (`ctx.confirm`) for destructive actions.
* Don't copy code into this repo that you don't have the right to contribute, or that is under licenses incompatible with MIT
  (for example GPL code).

## License of contributions
MODI is licensed under the [MIT License](LICENSE). **By submitting a pull request you agree that your contribution is licensed under the MIT License**, and that you wrote it yourself (or have the right to submit it) and it contains no secrets.

## Report a bug / suggest a feature
Open an [issue](../../issues/new/choose) and pick a template. Include your OS, MODI version (Settings panel, bottom right) and what you expected.
Attach `bridge.log` from MODI's data folder if relevant - **check it for private info first**.
Security problems: don't open a public issue, see [SECURITY.md](SECURITY.md).

## Run it locally
```bash
git clone https://github.com/<you>/modi.git        # your fork
cd modi
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r bridge/requirements.txt
python bridge/bridge.py                            # then open http://127.0.0.1:8765
```
Use `MODI_HOME=/some/tmp/dir` to keep test data away from a real install. Without a running Hermes you can use the mock:
`python bridge/dev/mock_hermes.py` (listens on 8642) and `HERMES_API_KEY=test-key-0123456789abcdef`.
Useful: `bridge/dev/shot_v3.py` (browser end-to-end test, needs Playwright), `bridge/dev/test_update.py` (updater tests).
Build the installers: `python tools/build.py` (needs `Pillow` only for `tools/make_icons.py`).

## Submit a pull request
1. **Fork** the repo on GitHub and create a branch: `git checkout -b my-change`.
2. Make a focused change; keep Windows scripts ASCII + CRLF-safe (the build handles line endings) and PowerShell 5.1 compatible.
3. Test what you touched (see above). For installer changes, say how you tested them.
4. Push to your fork and open a **pull request** against `main`; fill in the template.
5. Wait for review. The maintainer may ask for changes; small, well-explained PRs get merged fastest.

New features should be feature modules where possible (`bridge/modules/`, see `bridge/modules/_example.py` and the "Feature modules" section of the guide).
