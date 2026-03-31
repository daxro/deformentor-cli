"""Deformentor CLI - fetch data from InfoMentor."""

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from importlib.metadata import version as _pkg_version, PackageNotFoundError

import requests
from dotenv import dotenv_values

try:
    import argcomplete
    _HAS_ARGCOMPLETE = True
except ImportError:
    _HAS_ARGCOMPLETE = False

from deformentor_cli.errors import (
    FrejaError, emit_error, EXIT_AUTH, EXIT_NETWORK, EXIT_NOT_FOUND, EXIT_USAGE,
)
from deformentor_cli.api import (
    fetch_all_notifications, fetch_all_messages, get_attachment, get_attendance_detail,
    get_calendar_event, get_children, get_meeting_availabilities, get_news_detail, switch_child,
)
from deformentor_cli.paths import CONFIG_DIR, CONFIG_FILE, SESSION_FILE

_LOGO_LINES = [
    r"    _      __                       _               ___ _    ___ ",
    r" __| |___ / _|___ _ _ _ __  ___ _ _| |_ ___ _ _    / __| |  |_ _|",
    r"/ _` / -_)  _/ _ \ '_| '  \/ -_) ' \  _/ _ \ '_|  | (__| |__ | | ",
    r"\__,_\___|_| \___/_| |_|_|_\___|_||_\__\___/_|     \___|____|___|",
]
_CYAN = "\033[36m"
_BOLD_WHITE = "\033[1m\033[97m"
_RESET = "\033[0m"
# Split point: "deformentor" block width is 49 chars, then 2 spaces, then "CLI" block
_SPLIT = 49


def _should_use_color():
    """Check whether ANSI color codes should be emitted to stderr.

    Returns False if any of:
    - NO_COLOR env var is set (any value, per https://no-color.org)
    - TERM=dumb
    - stderr is not a TTY
    """
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stderr.isatty():
        return False
    return True


def print_logo(use_color=None):
    """Print the ASCII logo to stderr. Respects color settings."""
    if use_color is None:
        use_color = _should_use_color()
    for line in _LOGO_LINES:
        main_part = line[:_SPLIT]
        cli_part = line[_SPLIT + 2:] if len(line) > _SPLIT + 2 else ""
        if use_color:
            print(f"{_CYAN}{main_part}{_RESET}  {_BOLD_WHITE}{cli_part}{_RESET}", file=sys.stderr)
        else:
            print(f"{main_part}  {cli_part}", file=sys.stderr)
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
                except (requests.RequestException, RuntimeError):
                    pass
            except (requests.RequestException, RuntimeError):
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
        _output_json(status, args)
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
    os.chmod(CONFIG_FILE, 0o600)
    _progress(f"Saved to {CONFIG_FILE}", quiet)


def _progress(message, quiet=False):
    """Print progress message to stderr unless quiet mode is enabled."""
    if not quiet:
        print(message, file=sys.stderr)


def _get_version():
    """Get version from installed package metadata, with fallback."""
    try:
        return _pkg_version("deformentor-cli")
    except PackageNotFoundError:
        return "0.1.0-dev"


class _DeformentorParser(argparse.ArgumentParser):
    """ArgumentParser that emits JSON errors to stderr."""

    def error(self, message):
        error = {"error": "usage_error", "message": message}
        print(json.dumps(error), file=sys.stderr)
        self.exit(EXIT_USAGE)


def _configure_debug():
    """Enable debug logging of HTTP requests to stderr."""
    import logging
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.DEBUG)


def _filter_fields(data, fields):
    """Filter JSON output to only include specified fields.

    Supports dot-notation for nested fields (e.g., 'notifications.date').
    Returns data unchanged if fields is None.
    """
    if fields is None:
        return data
    if isinstance(data, list):
        return [_filter_fields(item, fields) for item in data]
    if not isinstance(data, dict):
        return data

    result = {}
    top_level = set()
    nested = {}
    for field in fields:
        parts = field.split(".", 1)
        if len(parts) == 1:
            top_level.add(parts[0])
        else:
            nested.setdefault(parts[0], []).append(parts[1])

    for key in top_level:
        if key in data:
            result[key] = data[key]

    for key, sub_fields in nested.items():
        if key in data:
            result[key] = _filter_fields(data[key], sub_fields)

    return result


def _output_json(data, args):
    """Print data as JSON to stdout, applying --fields filter if set."""
    fields = getattr(args, "fields", None)
    field_list = [f.strip() for f in fields.split(",")] if isinstance(fields, str) and fields else None
    data = _filter_fields(data, field_list)
    print(json.dumps(data, ensure_ascii=False, indent=2))


class _LogoHelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        use_color = _should_use_color()
        print_logo(use_color)
        parser.print_help(sys.stdout)
        parser.exit()


def main():
    parser = _DeformentorParser(
        prog="deformentor",
        description="Fetch school notifications and messages from InfoMentor via Freja eID+.",
        epilog="""examples:
  deformentor notifications                  Notifications from last 30 days
  deformentor notifications --child Anna     Filter by child
  deformentor notifications --type calendar  Filter by type
  deformentor messages --since 2026-01-01    Messages since a date
  deformentor news 12345                     News item detail
  deformentor attachment --url "/path" > file.pdf  Download attachment""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action=_LogoHelpAction, help="Show this message and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr")
    parser.add_argument("--no-input", action="store_true", help="Never prompt for input (fail if input would be needed)")
    parser.add_argument("--debug", action="store_true", help="Log HTTP requests and responses to stderr")
    parser.add_argument("--fields", help="Comma-separated list of fields to include in output")

    # Shared parent parser so -q is accepted after the subcommand name too
    _quiet = argparse.ArgumentParser(add_help=False)
    _quiet.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr")

    subparsers = parser.add_subparsers(dest="command", title="commands", parser_class=_DeformentorParser)
    subparsers.add_parser("setup", parents=[_quiet], help="Configure personnummer for login")
    notif_parser = subparsers.add_parser("notifications", parents=[_quiet],
        help="Fetch notifications and messages for all children",
        epilog="""examples:
  deformentor notifications --since all       All notifications, no date limit
  deformentor notifications --child Anna      Filter by child name
  deformentor notifications --type attendance  Filter by notification type""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    notif_parser.add_argument("--child", help="Filter by child firstname")
    notif_parser.add_argument("--type", help="Filter by type (attendance, calendar, news, meeting, message)")
    notif_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    notif_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). 'all' for no limit.")
    msg_parser = subparsers.add_parser("messages", parents=[_quiet],
        help="Fetch messages for all children",
        epilog="""examples:
  deformentor messages --child Anna       Messages for one child
  deformentor messages --since 2026-01-01  Messages since a date
  deformentor messages --since all         All messages""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    msg_parser.add_argument("--child", help="Filter by child firstname")
    msg_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    msg_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). 'all' for no limit.")
    msg_parser.add_argument("--all-pages", action="store_true", help="Fetch all message pages (default: page 1 only)")
    msg_parser.add_argument("--max-pages", type=int, default=50, help="Maximum pages to fetch with --all-pages (default: 50)")
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
    att2_parser = subparsers.add_parser("attachment", parents=[_quiet],
        help="Fetch an attachment and write bytes to stdout",
        epilog="""examples:
  deformentor attachment --url "/Resources/Resource/Download/123?api=IM2" > doc.pdf""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    att2_parser.add_argument("--url", required=True, help="Attachment URL path (from news detail attachments[].url)")
    att2_parser.add_argument("--child", help="Switch to this child's context before fetching")
    status_parser = subparsers.add_parser("status", parents=[_quiet], help="Show configuration and session status")
    status_parser.add_argument("--json", dest="json_output", action="store_true", help="Output status as JSON to stdout")

    if _HAS_ARGCOMPLETE:
        argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if getattr(args, "debug", False):
        _configure_debug()

    if args.command is None:
        use_color = _should_use_color()
        print_logo(use_color)
        parser.print_help(sys.stdout)
        sys.exit(1)

    try:
        if args.command == "setup":
            _setup(quiet=args.quiet, no_input=getattr(args, "no_input", False))
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
    except KeyboardInterrupt:
        sys.exit(130)
    except FrejaError as e:
        emit_error("auth_failed", f"Freja authentication failed: {e}", exit_code=EXIT_AUTH)
    except requests.HTTPError as e:
        emit_error("http_error", f"HTTP error: {e}", exit_code=EXIT_NETWORK)
    except requests.Timeout:
        emit_error("request_timeout", "Request timed out.", exit_code=EXIT_NETWORK)
    except requests.ConnectionError:
        emit_error("connection_failed", "Connection failed. Check your network.", exit_code=EXIT_NETWORK)


def _setup(quiet=False, no_input=False):
    if not no_input and sys.stdin.isatty():
        print_logo(_should_use_color())
    if no_input or not sys.stdin.isatty():
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
        login(personnummer, session_path=str(SESSION_FILE), quiet=quiet)
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


def _get_session(quiet=False):
    """Authenticate and return a session. Exits if not configured."""
    config = dotenv_values(CONFIG_FILE)
    personnummer = config.get("PERSONNUMMER")
    if not personnummer:
        emit_error("not_configured", "PERSONNUMMER not set. Run: deformentor setup", exit_code=EXIT_AUTH)
    return login(personnummer, session_path=str(SESSION_FILE), quiet=quiet)


def _notifications(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    session = _get_session(quiet=args.quiet)
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
    _output_json(result, args)


def _messages(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    session = _get_session(quiet=args.quiet)
    _progress("Fetching messages...", args.quiet)
    fetch_all_pages = getattr(args, "all_pages", False)
    max_pages = getattr(args, "max_pages", 50)
    result = fetch_all_messages(session, fetch_all_pages=fetch_all_pages, max_pages=max_pages)
    result = _filter_children(result, args.child)
    if args.child and not result:
        print(f"Warning: no child matching '{args.child}'", file=sys.stderr)
    for entry in result:
        entry["messages"] = _filter_items_since(entry["messages"], since)
        entry["messages"] = _filter_items_until(entry["messages"], until)
    _output_json(result, args)


def _calendar(args):
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching calendar event...", args.quiet)
    result = get_calendar_event(session, args.id)
    _output_json(result, args)


def _attendance(args):
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching attendance detail...", args.quiet)
    result = get_attendance_detail(session, args.id)
    _output_json(result, args)


def _news(args):
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching news item...", args.quiet)
    result = get_news_detail(session, args.id)
    if result is None:
        emit_error("not_found", f"News item {args.id} not found.", exit_code=EXIT_NOT_FOUND)
    _output_json(result, args)


def _meeting(args):
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching meeting availabilities...", args.quiet)
    result = get_meeting_availabilities(session)
    _output_json(result, args)


def _attachment(args):
    if sys.stdout.isatty():
        emit_error("usage_error", "Binary output. Redirect to a file: deformentor attachment --url <path> > file.pdf", exit_code=EXIT_USAGE)
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching attachment...", args.quiet)
    try:
        data = get_attachment(session, args.url)
    except ValueError as e:
        emit_error("invalid_input", str(e), exit_code=EXIT_USAGE)
    if not data:
        emit_error("not_found", "Attachment not found or empty response.", exit_code=EXIT_NOT_FOUND)
    sys.stdout.buffer.write(data)
