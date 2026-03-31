# Agent Guide

How to use deformentor-cli from automated scripts and AI agents.

`dfm` is an alias for `deformentor` and can be used interchangeably in all examples below.

## Install

```bash
uv tool install git+https://github.com/daxro/deformentor-cli.git
```

## Setup

Setup requires a personnummer and Freja eID+ phone approval. The session is cached for reuse after the first login.

**If setup has never been run (exit code 3 with "not_configured" error):**

1. Ask the user for their 12-digit personnummer
2. Run setup: `PERSONNUMMER=<value> deformentor setup --no-input -q`
3. Tell the user to approve the Freja eID+ prompt on their phone (times out after 60s)

```bash
PERSONNUMMER=200001011234 deformentor setup --no-input -q
```

`--no-input` only affects the `setup` command (it skips the interactive personnummer prompt). Other commands never prompt for input, so `--no-input` is not needed on them.

Once setup completes, the agent can run all other commands directly.

## Commands

```bash
deformentor notifications -q                    # JSON array of all notifications
deformentor notifications -q --child Anna       # Filter by child
deformentor notifications -q --type attendance  # Filter by type
deformentor messages -q                         # JSON array of messages
deformentor messages -q --all-pages             # Fetch all message pages
deformentor notifications -q --since 2026-01-01 --until 2026-03-31  # Date range
deformentor calendar <id> -q                    # Calendar event detail
deformentor attendance <id> -q                  # Leave request detail
deformentor news <id> -q                        # News item with attachments
deformentor meeting -q                          # Meeting availabilities
deformentor attachment --url <path> -q > f.pdf  # Download attachment (redirect required)
deformentor status --json                       # Machine-readable status check
deformentor reset -q                            # Remove all config and session
```

The `attachment` command requires stdout to be redirected to a file. It exits with code 2 if output goes to a terminal.

## Flags

| Flag | Description |
|------|-------------|
| -q / --quiet | Suppress progress messages on stderr |
| --no-input | Skip interactive prompts (setup only) |
| --fields x,y | Filter output to specific fields |
| --debug | Log HTTP requests to stderr |
| --since DATE | Start date (YYYY-MM-DD or 'all'). Default: 30 days ago |
| --until DATE | End date (YYYY-MM-DD or 'all'). No default upper bound |
| --max-pages N | Max pages to fetch with --all-pages (default 50, messages only) |
| --child NAME | Filter or switch by child name (case-insensitive substring) |
| --type TYPE | Filter notification type (attendance, calendar, news, meeting, message) |
| --version | Show version and exit |
| --json | (status only) Output as JSON |

## Error handling

Errors are JSON on stderr with distinct exit codes:

```json
{"error": "error_code", "message": "Human-readable message"}
```

| Exit code | Meaning |
|-----------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid input / usage error |
| 3 | Authentication error |
| 4 | Resource not found |
| 5 | Network error |

## Parsing output

All data commands output valid JSON to stdout. Use jq or json.loads():

```bash
deformentor notifications -q | jq '.[0].notifications[].type.name'
deformentor status --json | jq '.session'
```

## Session management

Sessions expire. When a session is expired, the CLI automatically re-authenticates on the next command - but this requires the user to approve on their phone again. If a command takes longer than expected or fails with exit code 3, tell the user to check their phone for a Freja eID+ approval prompt.

Note: `-q` suppresses the "approve in Freja" stderr message. If a command hangs, assume re-authentication is in progress and inform the user.

Setup only needs to be re-run if the personnummer config is missing (error message contains "not_configured"). Expired sessions do not require re-running setup.

The `status --json` command checks session validity without side effects.
