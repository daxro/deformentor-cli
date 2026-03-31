# Agent Guide

How to use deformentor-cli from automated scripts and AI agents.

## Setup (non-interactive)

```bash
PERSONNUMMER=200001011234 deformentor setup --no-input -q
```

Requires Freja eID+ phone approval. The session is cached for reuse.

## Commands

```bash
deformentor notifications -q                    # JSON array of all notifications
deformentor notifications -q --child Anna       # Filter by child
deformentor notifications -q --type attendance  # Filter by type
deformentor messages -q                         # JSON array of messages
deformentor messages -q --all-pages             # Fetch all message pages
deformentor calendar <id> -q                    # Calendar event detail
deformentor attendance <id> -q                  # Leave request detail
deformentor news <id> -q                        # News item with attachments
deformentor meeting -q                          # Meeting availabilities
deformentor attachment --url <path> -q > f.pdf  # Download attachment
deformentor status --json                       # Machine-readable status check
```

## Flags

| Flag | Description |
|------|-------------|
| -q / --quiet | Suppress progress messages on stderr |
| --no-input | Never prompt for input |
| --fields x,y | Filter output to specific fields |
| --debug | Log HTTP requests to stderr |
| --json | (status only) Output as JSON |

## Error handling

Errors are JSON on stderr with distinct exit codes:

```json
{"error": "error_code", "message": "Human-readable message"}
```

| Exit code | Meaning |
|-----------|---------|
| 0 | Success |
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

Sessions expire. If you get exit code 3, re-run setup.
The `status --json` command checks session validity without side effects.
