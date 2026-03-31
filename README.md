# deformentor-cli

An unofficial CLI for InfoMentor, using Freja eID+ for login.

For AI agents - make sure to read this README in full before taking action, since there are agent specific instructions (e.g. for setup).

## Prerequisites

- Python 3.10+
- Freja eID+ configured for Stockholms stad (parent account)

BankID is not supported since it does not support remote login.

## Install

```bash
uv tool install git+https://github.com/daxro/deformentor-cli.git
```

The short alias `dfm` is also available (e.g., `dfm notifications`).

## Setup

```bash
deformentor setup
```

Enter your 12-digit personnummer when prompted, then approve the login in Freja on your phone. The session is cached for reuse.

Non-interactive (pass personnummer via env var, still requires phone approval):

```bash
PERSONNUMMER=200001011234 deformentor setup --no-input
```

For agents: do NOT run `deformentor setup` directly or tell the user to run it - it requires interactive input you cannot provide. Instead:

1. Ask the user: "What is your 12-digit personnummer?"
2. Run: `PERSONNUMMER=<their-value> deformentor setup --no-input -q`
3. Tell the user: "Approve the Freja eID+ prompt on your phone now (60s timeout)"

This applies whenever a command fails with exit code 3 and `"not_configured"` error. `--no-input` only affects `setup` - other commands never prompt for input.

## Usage

```bash
deformentor notifications                  # notifications (default: last 30 days)
deformentor notifications --since all      # all notifications, no date limit
deformentor notifications --child Anna     # filter by child
deformentor notifications --type calendar  # filter by type
deformentor notifications --since 2026-01-01 --until 2026-03-31
deformentor messages                       # messages (default: last 30 days)
deformentor messages --all-pages           # fetch all message pages
deformentor messages --all-pages --max-pages 10  # fetch up to 10 pages
deformentor calendar <id>                  # calendar event detail
deformentor attendance <id>                # leave request detail
deformentor news <id>                      # news item detail
deformentor news <id> --child Anna         # news item for specific child
deformentor meeting                        # meeting slot availabilities
deformentor meeting --child Anna           # meeting slots for specific child
deformentor attachment --url "/path" > f.pdf  # download attachment to file
deformentor attachment --url "/path" --child Anna > f.pdf
```

`--child` works on all item commands: `calendar`, `attendance`, `news`, `meeting`, and `attachment`. Matching is case-insensitive substring against the full child name.

```bash
deformentor status                         # human-readable status
deformentor status --json                  # machine-readable status
deformentor reset                          # remove config and session files
```

All data commands output JSON to stdout. Progress messages go to stderr (suppress with `-q`). Use `--fields date,type` to filter output fields, `--debug` to log HTTP traffic, `--version` to print the installed version.

The `attachment` command writes raw bytes to stdout. Redirect to a file - it exits with code 2 if output goes to a terminal.

For agents: pipe through `jq` for field extraction, e.g. `deformentor notifications -q | jq '.[0].notifications[].type.name'`.

## Flags

| Flag | Description |
|------|-------------|
| -q / --quiet | Suppress progress messages on stderr |
| --no-input | Skip interactive prompts (setup only) |
| --fields x,y | Filter output to specific fields |
| --debug | Log HTTP requests to stderr |
| --since DATE | Start date (YYYY-MM-DD or 'all'). Default: 30 days ago |
| --until DATE | End date (YYYY-MM-DD or 'all'). No default upper bound |
| --all-pages | Fetch all pages (messages only) |
| --max-pages N | Max pages to fetch with --all-pages (default 50, messages only) |
| --child NAME | Filter or switch by child name (case-insensitive substring) |
| --type TYPE | Filter notification type (attendance, calendar, news, meeting, message) |
| --json | Output as JSON (status only) |

## Configuration

Config and state are stored in platform-standard directories (via [platformdirs](https://pypi.org/project/platformdirs/)):

| File | Linux | macOS |
|------|-------|-------|
| Config | `~/.config/deformentor/config.env` | `~/Library/Application Support/deformentor/config.env` |
| Session | `~/.local/state/deformentor/session.json` | `~/Library/Application Support/deformentor/session.json` |

Run `deformentor status --json` to see the actual paths on your system.

Optional config variables in `config.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_SINCE_DAYS` | `30` | Rolling window for `--since` default |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid input / usage error |
| 3 | Authentication error |
| 4 | Resource not found |
| 5 | Network error |

Errors are emitted as JSON to stderr:

```json
{"error": "auth_failed", "message": "Freja authentication failed: Authentication was rejected in the Freja app"}
```

For agents: sessions expire and the CLI re-authenticates automatically, which requires phone approval. If a command hangs or returns exit code 3, tell the user to check their phone for a Freja prompt. `-q` suppresses the "approve in Freja" stderr message, so assume re-auth is in progress. Setup only needs re-running if the error contains `"not_configured"` - expired sessions are handled automatically.

## Output

Notifications:

```json
[
  {
    "child": "Surname, Firstname",
    "child_id": "1234567",
    "notifications": [
      {
        "date": "2026-03-30T06:07:04",
        "type": {
          "name": "attendance",
          "id": "197608",
          "action": "LeaveRequestUpdated",
          "title": "Ledighetsansökan har uppdaterats"
        }
      }
    ]
  }
]
```

Messages:

```json
[
  {
    "child": "Surname, Firstname",
    "child_id": "1234567",
    "messages": [
      {
        "id": "11880746",
        "subject": "Viktig information",
        "from": "Larsson, Emelie",
        "date": "2025-10-20"
      }
    ]
  }
]
```

Notification types: `attendance`, `calendar`, `news`, `meeting`, `message`.

News item (`deformentor news <id>`):

```json
{
  "id": 197608,
  "title": "Viktig information",
  "content": "<p>HTML content...</p>",
  "publishedDate": "2026-03-01T08:00:00",
  "attachments": [
    {
      "url": "/Resources/Resource/Download/abc123",
      "title": "Bilaga.docx",
      "fileType": "docx"
    }
  ]
}
```

Meeting availabilities (`deformentor meeting`):

```json
{
  "totalCount": 12,
  "totalPages": 1,
  "availabilities": [
    {
      "availabilityId": "55001",
      "date": "2026-04-10",
      "timeFrom": "08:00",
      "timeRange": "08:00-08:20",
      "meetingType": "InPerson",
      "location": "Room 3",
      "meetingId": null
    }
  ]
}
```

## Uninstall

```bash
deformentor reset -q                       # remove config and session files
uv tool uninstall deformentor-cli          # remove the binary
```

## Development

For contributing or running from source:

```bash
git clone https://github.com/daxro/deformentor-cli.git
cd deformentor-cli
uv sync
uv run deformentor setup
```

## Testing

```bash
uv run pytest
```
