"""Deformentor CLI - fetch data from InfoMentor."""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

import requests
from dotenv import dotenv_values

from deformentor_cli.errors import (
    FrejaError, emit_error, EXIT_AUTH, EXIT_NETWORK, EXIT_NOT_FOUND, EXIT_USAGE,
)
from deformentor_cli.api import (
    fetch_all_notifications, fetch_all_messages, get_attachment, get_attendance_detail,
    get_calendar_event, get_children, get_meeting_availabilities, get_news_detail, switch_child,
)
from deformentor_cli.paths import CONFIG_DIR, CONFIG_FILE, SESSION_FILE, STATE_DIR
from deformentor_cli.session import login, new_session, load_session, verify_authenticated

KNOWN_NOTIFICATION_TYPES = {"attendance", "calendar", "news", "meeting", "message"}

_DEFAULT_SINCE_DAYS = 30
# _DEFAULT_UNTIL_DAYS: no default upper bound yet. Reserved for future lookup.


def _mask_personnummer(pnr):
    """200001011234 -> 0001****1234. Short input returned as-is."""
    if len(pnr) < 9:
        return pnr
    return pnr[2:6] + "****" + pnr[8:]


def _get_status():
    """Build status dict from config and session state."""
    config = dotenv_values(CONFIG_FILE)
    personnummer = config.get("PERSONNUMMER")

    status = {
        "configured": bool(personnummer),
        "personnummer": _mask_personnummer(personnummer) if personnummer else None,
        "session": None,
        "children": [],
    }

    if personnummer:
        session = new_session()
        if load_session(session, str(SESSION_FILE)):
            try:
                verify_authenticated(session)
                status["session"] = "valid"
                try:
                    children = get_children(session)
                    status["children"] = [{"name": c["name"], "id": c["id"]} for c in children]
                except Exception:
                    pass
            except Exception:
                status["session"] = "expired"
        else:
            status["session"] = "none"

    return status


def _print_status(status):
    """Print human-readable status to stderr."""
    if not status["configured"]:
        emit_error("not_configured", "Not configured. Run: deformentor setup", exit_code=EXIT_AUTH)

    print(f"Personnummer: {status['personnummer']}", file=sys.stderr)
    print(f"Session: {status['session']}", file=sys.stderr)
    if status["session"] == "expired":
        print("  Run any command to re-authenticate.", file=sys.stderr)
    if status["session"] == "none":
        emit_error("no_session", "No saved session.", exit_code=EXIT_AUTH)
    if status["children"]:
        print("Children:", file=sys.stderr)
        for child in status["children"]:
            name = child["name"].split(", ")[-1] if ", " in child["name"] else child["name"]
            print(f"  - {name} (id: {child['id']})", file=sys.stderr)


def _status(args):
    status = _get_status()
    if args.json_output:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    _print_status(status)


def _validate_date_flag(value, flag_name):
    """Validate a date flag is YYYY-MM-DD or 'all'. Returns value, None, or exits."""
    if value is None:
        return None
    if value.lower() == "all":
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        emit_error("invalid_input", f"{flag_name} must be YYYY-MM-DD or 'all'.", exit_code=EXIT_USAGE)
    return value


def _resolve_since(cli_value, config):
    """Resolve effective --since date. Priority: explicit flag > env var > default 30 days."""
    if cli_value is not None:
        return _validate_date_flag(cli_value, "--since")
    days_str = config.get("DEFAULT_SINCE_DAYS")
    if days_str is not None:
        if not days_str.lstrip("-").isdigit() or int(days_str) <= 0:
            emit_error("invalid_input", f"DEFAULT_SINCE_DAYS must be a positive integer, got '{days_str}'.", exit_code=EXIT_USAGE)
        days = int(days_str)
    else:
        days = _DEFAULT_SINCE_DAYS
    return (date.today() - timedelta(days=days)).isoformat()


def _resolve_until(cli_value):
    """Resolve effective --until date. Only explicit flag supported for now."""
    if cli_value is not None:
        return _validate_date_flag(cli_value, "--until")
    # Future: read DEFAULT_UNTIL_DAYS from config and compute date.today() + timedelta(days=days)
    return None


def _filter_children(results, firstname):
    """Filter result list by child firstname (case-insensitive). None = no filter."""
    if firstname is None:
        return results
    firstname_lower = firstname.lower()
    return [r for r in results if firstname_lower in r["child"].lower()]


def _filter_items_by_type(items, type_name):
    """Filter notification items by type name (case-insensitive). None = no filter."""
    if type_name is None:
        return items
    type_lower = type_name.lower()
    return [item for item in items if item["type"]["name"] == type_lower]


def _filter_items_since(items, since):
    """Filter items where date >= since (string comparison). None = no filter."""
    if since is None:
        return items
    return [item for item in items if item["date"] >= since]


def _filter_items_until(items, until):
    """Filter items where date <= until (date-part comparison). None = no filter."""
    if until is None:
        return items
    return [item for item in items if item["date"][:10] <= until]


def _resolve_and_switch_child(session, firstname):
    """Find child by firstname (case-insensitive) and switch session context.

    Exits if no match. Warns if multiple match (uses first).
    """
    children = get_children(session)
    firstname_lower = firstname.lower()
    matches = [c for c in children if firstname_lower in c["name"].lower()]
    if not matches:
        emit_error("child_not_found", f"No child matching '{firstname}'.", exit_code=EXIT_NOT_FOUND)
    if len(matches) > 1:
        names = ", ".join(c["name"] for c in matches)
        print(f"Warning: multiple children match '{firstname}': {names}. Using first match.", file=sys.stderr)
    switch_child(session, matches[0]["id"])


def _write_config(content, quiet=False):
    """Write config content to CONFIG_FILE, creating directories as needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(content)
    _progress(f"Saved to {CONFIG_FILE}", quiet)


def _progress(message, quiet=False):
    """Print progress message to stderr unless quiet mode is enabled."""
    if not quiet:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        prog="deformentor",
        description="Fetch school notifications and messages from InfoMentor via Freja eID+.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr")

    # Shared parent parser so -q is accepted after the subcommand name too
    _quiet = argparse.ArgumentParser(add_help=False)
    _quiet.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr")

    subparsers = parser.add_subparsers(dest="command", title="commands")
    subparsers.add_parser("setup", parents=[_quiet], help="Configure personnummer for login")
    notif_parser = subparsers.add_parser("notifications", parents=[_quiet], help="Fetch notifications and messages for all children")
    notif_parser.add_argument("--child", help="Filter by child firstname")
    notif_parser.add_argument("--type", help="Filter by type (attendance, calendar, news, meeting, message)")
    notif_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    # notif_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). Default: no limit. 'all' to disable.")
    msg_parser = subparsers.add_parser("messages", parents=[_quiet], help="Fetch messages for all children")
    msg_parser.add_argument("--child", help="Filter by child firstname")
    msg_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    # msg_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). Default: no limit. 'all' to disable.")
    cal_parser = subparsers.add_parser("calendar", parents=[_quiet], help="Fetch a calendar event by ID")
    cal_parser.add_argument("id", help="Calendar event ID (from notifications output)")
    cal_parser.add_argument("--child", help="Switch to this child's context before fetching")
    att_parser = subparsers.add_parser("attendance", parents=[_quiet], help="Fetch an attendance / leave request by ID")
    att_parser.add_argument("id", help="Attendance/leave request ID (from notifications output)")
    att_parser.add_argument("--child", help="Switch to this child's context before fetching")
    news_parser = subparsers.add_parser("news", parents=[_quiet], help="Fetch a news item by ID")
    news_parser.add_argument("id", help="News item ID (from notifications output)")
    news_parser.add_argument("--child", help="Switch to this child's context before fetching")
    meeting_parser = subparsers.add_parser("meeting", parents=[_quiet], help="Fetch meeting slot availabilities for a child")
    meeting_parser.add_argument("--child", help="Switch to this child's context before fetching")
    att2_parser = subparsers.add_parser("attachment", parents=[_quiet], help="Fetch an attachment and write bytes to stdout")
    att2_parser.add_argument("url", help="Attachment URL path (from news detail attachments[].url)")
    att2_parser.add_argument("--child", help="Switch to this child's context before fetching")
    status_parser = subparsers.add_parser("status", help="Show configuration and session status")
    status_parser.add_argument("--json", dest="json_output", action="store_true", help="Output status as JSON to stdout")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == "setup":
            _setup(quiet=args.quiet)
        elif args.command == "notifications":
            _notifications(args)
        elif args.command == "messages":
            _messages(args)
        elif args.command == "calendar":
            _calendar(args)
        elif args.command == "attendance":
            _attendance(args)
        elif args.command == "news":
            _news(args)
        elif args.command == "meeting":
            _meeting(args)
        elif args.command == "attachment":
            _attachment(args)
        elif args.command == "status":
            _status(args)
    except FrejaError as e:
        emit_error("auth_failed", f"Freja authentication failed: {e}", exit_code=EXIT_AUTH)
    except requests.HTTPError as e:
        emit_error("http_error", f"HTTP error: {e}", exit_code=EXIT_NETWORK)
    except requests.Timeout:
        emit_error("request_timeout", "Request timed out.", exit_code=EXIT_NETWORK)
    except requests.ConnectionError:
        emit_error("connection_failed", "Connection failed. Check your network.", exit_code=EXIT_NETWORK)


def _setup(quiet=False):
    if not sys.stdin.isatty():
        personnummer = os.environ.get("PERSONNUMMER")
        if not personnummer:
            emit_error(
                "setup_required",
                "PERSONNUMMER env var required in non-interactive mode.",
                exit_code=EXIT_USAGE,
            )
        if not personnummer.isdigit() or len(personnummer) != 12:
            emit_error("invalid_input", "Invalid personnummer. Must be 12 digits (YYYYMMDDXXXX).", exit_code=EXIT_USAGE)
        _write_config(f"PERSONNUMMER={personnummer}\n", quiet)
        login(personnummer, session_path=str(SESSION_FILE))
        _progress("Authenticated.", quiet)
        _print_status(_get_status())
        return

    existing = dotenv_values(CONFIG_FILE).get("PERSONNUMMER") if CONFIG_FILE.exists() else None
    if existing:
        print(f"Already configured (PERSONNUMMER={_mask_personnummer(existing)})", file=sys.stderr)
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            login(existing, session_path=str(SESSION_FILE))
            _progress("Authenticated.", quiet)
            _print_status(_get_status())
            return

    personnummer = input("Personnummer (12 digits): ").strip()
    if not personnummer.isdigit() or len(personnummer) != 12:
        emit_error("invalid_input", "Invalid personnummer. Must be 12 digits (YYYYMMDDXXXX).", exit_code=EXIT_USAGE)

    _write_config(f"PERSONNUMMER={personnummer}\n", quiet)

    login(personnummer, session_path=str(SESSION_FILE))
    _progress("Authenticated.", quiet)
    _print_status(_get_status())


def _get_session():
    """Authenticate and return a session. Exits if not configured."""
    config = dotenv_values(CONFIG_FILE)
    personnummer = config.get("PERSONNUMMER")
    if not personnummer:
        emit_error("not_configured", "PERSONNUMMER not set. Run: deformentor setup", exit_code=EXIT_AUTH)
    return login(personnummer, session_path=str(SESSION_FILE))


def _notifications(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    session = _get_session()
    _progress("Fetching notifications...", args.quiet)
    result = fetch_all_notifications(session)
    result = _filter_children(result, args.child)
    if args.child and not result:
        print(f"Warning: no child matching '{args.child}'", file=sys.stderr)
    for entry in result:
        entry["notifications"] = _filter_items_by_type(entry["notifications"], args.type)
        entry["notifications"] = _filter_items_since(entry["notifications"], since)
        entry["notifications"] = _filter_items_until(entry["notifications"], until)
    if args.type and args.type.lower() not in KNOWN_NOTIFICATION_TYPES:
        print(f"Warning: '{args.type}' is not a known type. Known types: {', '.join(sorted(KNOWN_NOTIFICATION_TYPES))}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _messages(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    session = _get_session()
    _progress("Fetching messages...", args.quiet)
    result = fetch_all_messages(session)
    result = _filter_children(result, args.child)
    if args.child and not result:
        print(f"Warning: no child matching '{args.child}'", file=sys.stderr)
    for entry in result:
        entry["messages"] = _filter_items_since(entry["messages"], since)
        entry["messages"] = _filter_items_until(entry["messages"], until)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _calendar(args):
    session = _get_session()
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching calendar event...", args.quiet)
    result = get_calendar_event(session, args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _attendance(args):
    session = _get_session()
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching attendance detail...", args.quiet)
    result = get_attendance_detail(session, args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _news(args):
    session = _get_session()
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching news item...", args.quiet)
    result = get_news_detail(session, args.id)
    if result is None:
        emit_error("not_found", f"News item {args.id} not found.", exit_code=EXIT_NOT_FOUND)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _meeting(args):
    session = _get_session()
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching meeting availabilities...", args.quiet)
    result = get_meeting_availabilities(session)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _attachment(args):
    session = _get_session()
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching attachment...", args.quiet)
    data = get_attachment(session, args.url)
    if not data:
        emit_error("not_found", "Attachment not found or empty response.", exit_code=EXIT_NOT_FOUND)
    sys.stdout.buffer.write(data)
