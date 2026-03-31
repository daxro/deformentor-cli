# Deformentor

CLI that fetches school notifications and messages from InfoMentor via Stockholms stad's Freja eID+. Outputs JSON to stdout.

## Prerequisites

- Python 3.10+
- Freja eID+ configured for Stockholms stad (parent account)

BankID is not supported since it does not support remote login.

## Install

Global install (available everywhere):

```bash
uv tool install git+https://github.com/daxro/deformentor-cli.git
```

For development:

```bash
git clone https://github.com/daxro/deformentor-cli.git
cd deformentor-cli
uv sync
uv run deformentor --version
```

## Setup

Interactive:

```bash
deformentor setup
```

Prompts for your 12-digit personnummer, then authenticates via Freja eID+ (approve on your phone).

Non-interactive (for agents/CI):

```bash
PERSONNUMMER=200001011234 deformentor setup --no-input
```

## Configuration

Config and state are stored in XDG-standard directories:

| File | Path | Contents |
|------|------|----------|
| Config | `~/.config/deformentor/config.env` | `PERSONNUMMER`, `DEFAULT_SINCE_DAYS` |
| Session | `~/.local/state/deformentor/session.json` | Cached auth cookies |

Optional config variables in `config.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_SINCE_DAYS` | `30` | Rolling window for `--since` default |

## Usage

```bash
deformentor notifications                  # last 30 days of notifications
deformentor notifications --since all      # all notifications, no date limit
deformentor notifications --child Anna     # filter by child
deformentor notifications --type calendar  # filter by type
deformentor notifications --since 2026-01-01 --until 2026-03-31
deformentor messages                       # messages only (last 30 days)
deformentor messages --all-pages           # fetch all message pages
deformentor calendar <id>                  # calendar event detail
deformentor attendance <id>                # leave request detail
deformentor news <id>                      # news item detail
deformentor news <id> --child Anna         # news item for specific child
deformentor meeting                        # meeting slot availabilities
deformentor attachment --url "/path" > f.pdf  # download attachment to file
deformentor status                         # human-readable status
deformentor status --json                  # machine-readable status
```

All data commands output JSON to stdout. Progress messages go to stderr (suppress with `-q`). Use `--fields date,type` to filter output fields, `--debug` to log HTTP traffic.

The short alias `dfm` is also available (e.g., `dfm notifications`).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid input |
| 3 | Authentication error |
| 4 | Resource not found |
| 5 | Network error |

Errors are emitted as JSON to stderr:

```json
{"error": "auth_failed", "message": "Freja authentication failed: Rejected"}
```

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

`deformentor attachment` writes raw bytes to stdout. Redirect to a file:

```bash
deformentor attachment --url "/Resources/Resource/Download/abc123" > doc.docx
```

## Testing

```bash
uv run pytest
```
