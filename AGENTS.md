# Repository Guide

## Run locally

- Start (from the repository root): `./run.ps1` in PowerShell, or `run.bat` in Command Prompt. The launcher creates/repairs `backend/venv`, installs Chromium, and runs the app. The port defaults to `8001`; override it with `./run.ps1 -Port 9000` or `run.bat 9000`.
- Stop: press `Ctrl+C` in the launcher terminal. Do not kill unrelated Python processes.
- The only expected listener is `127.0.0.1:8001` by default. Check readiness with `Invoke-RestMethod http://127.0.0.1:8001/api/version`; also verify `http://127.0.0.1:8001/` returns the dashboard. Use the configured port when overridden, and do not expose the server on `0.0.0.0`.

## Checks

Run these commands from the repository root:

- Tests: `Push-Location backend; ./venv/Scripts/python.exe -m unittest test_p0_regressions.py; Pop-Location`
- Syntax/lint check: `Push-Location backend; ./venv/Scripts/python.exe -m compileall -q .; Pop-Location`
- Formatting: no formatter is configured; preserve the existing Python, JavaScript, HTML, and CSS style and avoid unrelated reformatting.
- `backend/test_match_api.py` is a manual integration script that uses the local profile, database, and Gemini API; do not run it as part of routine tests.

## Database and fixtures

- SQLite lives at `data/jobapplier.db`. Schema creation and migrations run on import/startup in `backend/database.py`; migrations must be idempotent and safe for existing user data.
- Prefer additive migrations. Never reset, replace, seed, or destructively edit the user's database. Cover schema changes with temporary SQLite databases and mocked services, following `backend/test_p0_regressions.py`.
- There is no shared fixture set. Keep test data synthetic and temporary; tests must not depend on `data/`, real resumes, credentials, network calls, or paid APIs.

## Protected files

- Do not modify or commit local/private/generated content: `data/`, database files, `.env*`, `baseresume.*`, `resume.*`, PDFs, `backend/venv/`, caches, IDE settings, or OS metadata. Follow `.gitignore`, and never print or expose API keys or resume/profile data.
- Do not regenerate brand assets or icons unless the task explicitly requests it.

## Completion criteria

Before declaring work complete, inspect `git diff` and `git status`, run the P0 tests and syntax check above, and confirm no protected files are staged. For runtime or UI changes, start the app, verify `/api/version` and `/`, exercise the changed flow, then stop it cleanly. Report any check that could not be run and why.
