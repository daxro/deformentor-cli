# Context

Deformentor CLI fetches school data from InfoMentor via Stockholms stad's Freja eID+ authentication.

## Architecture

```
deformentor_cli/
  cli.py      - Argparse-based CLI entry point, command handlers
  api.py      - InfoMentor API calls, data normalization
  session.py  - Login chain: InfoMentor -> Stockholms stad -> Freja eID+
  freja.py    - Freja eID+ polling (submit personnummer, poll for phone approval)
  errors.py   - Structured JSON error output, exit codes
  paths.py    - XDG-compliant config/state paths
  __init__.py  - Public API re-exports (login, new_session, etc.)
```

## Data flow

1. User runs a command (e.g., `deformentor notifications`)
2. `cli.py` authenticates via `session.py` (reuses cached session if valid)
3. `api.py` fetches data from `hub.infomentor.se`, normalizes it
4. JSON output goes to stdout, progress/errors to stderr

## Authentication chain

InfoMentor uses federated login: InfoMentor -> Stockholms stad SSO -> Freja eID+.
The session module navigates this chain, including SAML form auto-submits.
Authentication requires physical approval on the user's phone via the Freja app.

## Key conventions

- All data output is JSON on stdout
- All errors are structured JSON on stderr: `{"error": "code", "message": "text"}`
- Exit codes: 0=success, 1=error, 2=usage, 3=auth, 4=not_found, 5=network
- Progress messages go to stderr, suppressed with -q/--quiet
- Config and session paths are platform-dependent (via `platformdirs`). Run `deformentor status --json` for actual paths.
  - Linux: `~/.config/deformentor/config.env`, `~/.local/state/deformentor/session.json`
  - macOS: `~/Library/Application Support/deformentor/config.env`, `~/Library/Application Support/deformentor/session.json`
