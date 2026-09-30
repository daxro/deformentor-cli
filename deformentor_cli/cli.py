"""Deformentor CLI - fetch data from InfoMentor."""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from importlib.metadata import version as _pkg_version, PackageNotFoundError

import requests
import portalocker
from dotenv import dotenv_values

try:
    import argcomplete
    _HAS_ARGCOMPLETE = True
except ImportError:
    _HAS_ARGCOMPLETE = False

from deformentor_cli.errors import (
    AuthenticationError, OAuthSetupRequired, UpstreamStateError, emit_error,
    EXIT_AUTH, EXIT_ERROR, EXIT_NETWORK, EXIT_NOT_FOUND, EXIT_USAGE,
)
from deformentor_cli.api import (
    create_leave_request, fetch_all_calendar_events, fetch_all_notifications, fetch_all_messages,
    get_attachment, get_attendance_app_data, get_attendance_detail, get_children,
    get_leave_requests, get_meeting_availabilities, get_news_detail,
    get_time_registration_comments, get_time_registration_for_date,
    normalize_time_registration_comment, report_full_day_absence,
    save_time_registration_comment, save_time_registration_day, switch_child,
    validate_attachment_url,
)
from deformentor_cli.paths import (
    CONFIG_FILE, OAUTH_FILE, OAUTH_LOCK_FILE, SESSION_FILE, write_private_text,
)
from deformentor_cli.session import (
    HTTP_TIMEOUT, login, load_session, new_session, oauth_state, save_oauth_credential,
    save_session, setup_login, verify_authenticated,
)
from stockholm_freja import FrejaError, FrejaHttpError, FrejaInputError, validate_personnummer

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


def _maybe_print_logo():
    """Print decorative branding only for an interactive stderr."""
    if sys.stderr.isatty():
        print_logo(_should_use_color())

KNOWN_NOTIFICATION_TYPES = {"attendance", "calendar", "news", "meeting", "message"}

_DEFAULT_SINCE_DAYS = 30
_CALENDAR_WINDOW_DAYS = 30


def _validate_personnummer(personnummer, stored=False):
    """Validate a personnummer without echoing it in errors."""
    try:
        return validate_personnummer(personnummer)
    except FrejaInputError:
        pass
    if stored:
        emit_error(
            "invalid_config",
            "Stored PERSONNUMMER must be 12 digits. Run: deformentor setup",
            exit_code=EXIT_USAGE,
        )
    emit_error("invalid_input", "Invalid personnummer. Must be 12 digits (YYYYMMDDXXXX).", exit_code=EXIT_USAGE)


def _get_public_oauth_state():
    """Return only a non-sensitive OAuth status literal for command output."""
    state = oauth_state(OAUTH_FILE)
    if state == "configured":
        return "configured"
    if state == "missing":
        return "missing"
    return "invalid"


def _get_status():
    """Build status dict from config and session state."""
    config = dotenv_values(CONFIG_FILE)
    personnummer = config.get("PERSONNUMMER")

    status = {
        "configured": bool(personnummer),
        "session": None,
        "oauth": _get_public_oauth_state(),
        "children": [],
        "config_path": str(CONFIG_FILE),
        "session_path": str(SESSION_FILE),
        "oauth_path": str(OAUTH_FILE),
    }

    if personnummer:
        _validate_personnummer(personnummer, stored=True)
        session = new_session()
        if load_session(session, str(SESSION_FILE)):
            try:
                verify_authenticated(session)
                status["session"] = "valid"
                children = get_children(session)
                status["children"] = [{"name": c["name"], "id": c["id"]} for c in children]
            except AuthenticationError:
                status["session"] = "expired"
        else:
            status["session"] = "none"

    status["can_authenticate_unattended"] = bool(
        status["configured"]
        and (status["session"] == "valid" or status["oauth"] == "configured")
    )
    status["action_required"] = (
        None if status["can_authenticate_unattended"] else "run_setup"
    )

    return status


def _print_status(status):
    """Print human-readable status to stdout."""
    if not status["configured"]:
        print("Ready: no")
        print("Not configured. Run: deformentor setup")
        return

    print(f"Config: {status['config_path']}")
    print(f"Session: {status['session']}")
    print(f"OAuth: {status['oauth']}")
    print(f"Ready: {'yes' if status['can_authenticate_unattended'] else 'no'}")
    if status["session"] == "expired":
        if status["oauth"] == "configured":
            print("  The next command will renew the session automatically.")
        else:
            print("  Run deformentor setup and approve Freja to enable automatic renewal.")
    if status["session"] == "none":
        if status["oauth"] == "configured":
            print("  The next command will create a session automatically.")
        else:
            print("  Run deformentor setup and approve Freja to enable automatic renewal.")
    if status["children"]:
        print("Children:")
        for child in status["children"]:
            name = child["name"].split(", ")[-1] if ", " in child["name"] else child["name"]
            print(f"  - {name} (id: {child['id']})")


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
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        emit_error("invalid_input", f"{flag_name} must be a real calendar date.", exit_code=EXIT_USAGE)


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


def _resolve_calendar_range(since_value, until_value):
    """Return the bounded, inclusive calendar query range before authentication."""
    def parse_bound(value, flag_name):
        if value is None:
            return None
        if value.lower() == "all":
            emit_error("invalid_input", f"{flag_name} must be a real calendar date.", exit_code=EXIT_USAGE)
        return _validate_date_flag(value, flag_name)

    today = date.today()
    since = parse_bound(since_value, "--since") or today.isoformat()
    until = parse_bound(until_value, "--until") or (today + timedelta(days=_CALENDAR_WINDOW_DAYS)).isoformat()
    if since > until:
        emit_error("invalid_input", "--since cannot be after --until.", exit_code=EXIT_USAGE)
    if (date.fromisoformat(until) - date.fromisoformat(since)).days > _CALENDAR_WINDOW_DAYS:
        emit_error(
            "invalid_input",
            f"Calendar date range cannot exceed {_CALENDAR_WINDOW_DAYS} days.",
            exit_code=EXIT_USAGE,
        )
    return since, until


def _validate_exact_pickup_date(value):
    """Validate exact YYYY-MM-DD date for agent-facing CLI usage."""
    normalized = (value or "").strip()
    if not normalized:
        emit_error("invalid_input", "--date is required for comment.", exit_code=EXIT_USAGE)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        emit_error("invalid_input", f"--date must be an exact YYYY-MM-DD date, got: {value}", exit_code=EXIT_USAGE)
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError:
        emit_error("invalid_input", f"Invalid pickup date: {value}", exit_code=EXIT_USAGE)


def _validate_exact_date(value, flag):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        emit_error("invalid_input", f"{flag} must be YYYY-MM-DD.", exit_code=EXIT_USAGE)
    try:
        return date.fromisoformat(value)
    except ValueError:
        emit_error("invalid_input", f"{flag} must be a real calendar date.", exit_code=EXIT_USAGE)


def _validate_time(value, flag):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        emit_error("invalid_input", f"{flag} must be HH:MM.", exit_code=EXIT_USAGE)
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59 or minute % 5:
        emit_error("invalid_input", f"{flag} must be a valid time on a five-minute boundary.", exit_code=EXIT_USAGE)
    return value


def _require_write_flags(args):
    if args.apply and not args.confirm:
        emit_error("invalid_input", "--apply requires --confirm.", exit_code=EXIT_USAGE)
    if args.confirm and not args.apply:
        emit_error("invalid_input", "--confirm requires --apply.", exit_code=EXIT_USAGE)


def _validate_comment_text(comment_text):
    """Validate exact comment text without silently trimming it."""
    comment_text = "" if comment_text is None else comment_text
    if comment_text == "" or not comment_text.strip():
        emit_error("invalid_input", "--comment must not be empty.", exit_code=EXIT_USAGE)
    return comment_text


def _validate_positive_decimal_id(value, label):
    """Validate documented numeric resource IDs before authentication."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
        emit_error("invalid_input", f"{label} must be a positive decimal ID.", exit_code=EXIT_USAGE)
    return value


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


def _child_first_name(child_name):
    """Return display/first-name component from InfoMentor child name."""
    return child_name.split(",")[-1].strip()


def _resolve_and_switch_child(session, firstname):
    """Find child by name (case-insensitive) and switch session context.

    Multiple substring matches must collapse to one exact first-name/full-name
    match. All context switches fail closed.
    """
    children = get_children(session)
    firstname_lower = firstname.lower()
    matches = [c for c in children if firstname_lower in c["name"].lower()]
    if not matches:
        emit_error("child_not_found", f"No child matching '{firstname}'.", exit_code=EXIT_NOT_FOUND)
    if len(matches) > 1:
        names = ", ".join(c["name"] for c in matches)
        exact_matches = [
            c for c in matches
            if c["name"].lower() == firstname_lower or _child_first_name(c["name"]).lower() == firstname_lower
        ]
        if len(exact_matches) == 1:
            matches = exact_matches
        else:
            emit_error(
                "ambiguous_child",
                f"'{firstname}' matches multiple children: {names}. Use a unique or exact child name.",
                exit_code=EXIT_USAGE,
            )
    switch_child(session, matches[0]["id"])
    return matches[0]


def _write_config(content, quiet=False):
    """Write config content to CONFIG_FILE, creating directories as needed."""
    write_private_text(CONFIG_FILE, content)
    _progress(f"Saved to {CONFIG_FILE}", quiet)


def _persist_setup_state(personnummer, session, oauth_credential, quiet=False):
    """Replace all setup state, restoring every prior file on save failure."""
    paths = (CONFIG_FILE, SESSION_FILE, OAUTH_FILE)
    previous = {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path in paths
    }
    try:
        _write_config(f"PERSONNUMMER={personnummer}\n", quiet=True)
        save_session(session, str(SESSION_FILE))
        save_oauth_credential(oauth_credential, OAUTH_FILE)
    except Exception:
        for path, content in previous.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                write_private_text(path, content)
        raise
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
        return "0.3.0-dev"


class _DeformentorParser(argparse.ArgumentParser):
    """ArgumentParser that emits JSON errors to stderr."""

    def error(self, message):
        error = {"error": "usage_error", "message": message}
        print(json.dumps(error), file=sys.stderr)
        self.exit(EXIT_USAGE)


def _configure_debug():
    """Enable sanitized HTTP response metadata logging to stderr."""
    import logging
    logger = logging.getLogger("deformentor_cli.http")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


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
    grouped = {}
    for field in fields:
        parts = field.split(".", 1)
        key = parts[0]
        if key not in grouped:
            grouped[key] = [] if len(parts) == 2 else None
        if len(parts) == 1:
            grouped[key] = None
        elif grouped[key] is not None:
            grouped[key].append(parts[1])

    for key, sub_fields in grouped.items():
        if key not in data:
            continue
        if sub_fields is None:
            result[key] = data[key]
        else:
            result[key] = _filter_fields(data[key], sub_fields)

    return result


def _output_json(data, args):
    """Print data as JSON to stdout, applying --fields filter if set."""
    fields = getattr(args, "fields", None)
    field_list = [f.strip() for f in fields.split(",")] if isinstance(fields, str) and fields else None
    if field_list and data and not any(_field_exists(data, field.split(".")) for field in field_list):
        emit_error(
            "field_not_found",
            f"None of the requested fields exist: {', '.join(field_list)}",
            exit_code=EXIT_USAGE,
        )
    data = _filter_fields(data, field_list)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _field_exists(data, path):
    """Return whether a dotted field exists in any non-empty result."""
    if isinstance(data, list):
        return not data or any(_field_exists(item, path) for item in data)
    if not isinstance(data, dict) or not path or path[0] not in data:
        return False
    if len(path) == 1:
        return True
    return _field_exists(data[path[0]], path[1:])


def _validate_fields_usage(args):
    """Reject empty, malformed, or ineffective --fields use."""
    fields = getattr(args, "fields", None)
    if fields is None:
        return
    field_list = [field.strip() for field in fields.split(",")]
    if not field_list or any(not field or any(not part for part in field.split(".")) for field in field_list):
        emit_error("invalid_input", "--fields must contain valid comma-separated dotted field names.", exit_code=EXIT_USAGE)
    if args.command in {"setup", "reset", "attachment"}:
        emit_error("invalid_input", f"--fields has no effect for {args.command}.", exit_code=EXIT_USAGE)
    if args.command == "status" and not args.json_output:
        emit_error("invalid_input", "--fields requires status --json.", exit_code=EXIT_USAGE)


class _LogoHelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        _maybe_print_logo()
        parser.print_help(sys.stdout)
        parser.exit()


def main():
    """Run the CLI and silence broken pipes, including buffered shutdown writes."""
    try:
        result = _run_cli()
    except BrokenPipeError:
        _exit_broken_pipe()
    except SystemExit:
        _flush_stdout_or_exit()
        raise
    _flush_stdout_or_exit()
    return result


def _flush_stdout_or_exit():
    if sys.stdout is None:
        return
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_broken_pipe()


def _exit_broken_pipe():
    """Redirect stdout away from a closed pipe and exit without a traceback."""
    try:
        stdout_fd = sys.stdout.fileno()
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull_fd, stdout_fd)
        finally:
            os.close(devnull_fd)
    except (AttributeError, OSError, ValueError):
        pass
    sys.stdout = None
    raise SystemExit(0)


def _run_cli():
    parser = _DeformentorParser(
        prog="deformentor",
        description="Fetch school notifications and messages from InfoMentor with reusable OAuth authentication.",
        epilog="""examples:
  deformentor notifications                  Notifications from last 30 days
  deformentor notifications --child CHILD_NAME  Filter by child
  deformentor notifications --type calendar  Filter by type
  deformentor messages --since 2026-01-01    Messages since a date
  deformentor comment --child CHILD_NAME --date 2026-06-05  Read time registration comment
  deformentor news 12345                     News item detail
  deformentor attachment --url "/path" > file.pdf  Download attachment""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action=_LogoHelpAction, help="Show this message and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress messages on stderr")
    parser.add_argument("--no-input", action="store_true", help="Never prompt for input (fail if input would be needed)")
    parser.add_argument("--debug", action="store_true", help="Log sanitized HTTP metadata to stderr")
    parser.add_argument("--fields", help="Comma-separated list of fields to include in output")

    # Parent parsers so global flags are accepted after the subcommand name too.
    # SUPPRESS on defaults prevents subparser defaults from clobbering root-parsed values.
    # _base_flags: flags shared by every subcommand.
    # _global_flags: _base_flags + --fields (only for commands that produce field-filterable output).
    _base_flags = argparse.ArgumentParser(add_help=False)
    _base_flags.add_argument("-q", "--quiet", action="store_true",
                             default=argparse.SUPPRESS, help="Suppress progress messages on stderr")
    _base_flags.add_argument("--no-input", action="store_true",
                             default=argparse.SUPPRESS, help="Never prompt for input (fail if input would be needed)")
    _base_flags.add_argument("--debug", action="store_true",
                             default=argparse.SUPPRESS, help="Log sanitized HTTP metadata to stderr")
    _global_flags = argparse.ArgumentParser(add_help=False, parents=[_base_flags])
    _global_flags.add_argument("--fields",
                               default=argparse.SUPPRESS, help="Comma-separated list of fields to include in output")

    subparsers = parser.add_subparsers(dest="command", title="commands", parser_class=_DeformentorParser)
    setup_parser = subparsers.add_parser("setup", parents=[_base_flags], help="Configure or renew OAuth login",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    setup_parser.add_argument(
        "--personnummer",
        help="Personnummer for initial setup or an account change; otherwise reuse the stored value",
    )
    notif_parser = subparsers.add_parser("notifications", parents=[_global_flags],
        help="Fetch notifications and messages for all children",
        epilog="""examples:
  deformentor notifications --since all       All notifications, no date limit
  deformentor notifications --child CHILD_NAME  Filter by child name
  deformentor notifications --type attendance  Filter by notification type""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    notif_parser.add_argument("--child", help="Filter by child name (case-insensitive substring match)")
    notif_parser.add_argument("--type", help="Filter by type (attendance, calendar, news, meeting, message)")
    notif_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    notif_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). 'all' for no limit.")
    msg_parser = subparsers.add_parser("messages", parents=[_global_flags],
        help="Fetch messages for all children",
        epilog="""examples:
  deformentor messages --child CHILD_NAME  Messages for one child
  deformentor messages --since 2026-01-01  Messages since a date
  deformentor messages --since all         All messages""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    msg_parser.add_argument("--child", help="Filter by child name (case-insensitive substring match)")
    msg_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: 30 days ago. 'all' for no limit.")
    msg_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). 'all' for no limit.")
    msg_parser.add_argument("--all-pages", action="store_true", help="Fetch all message pages (default: page 1 only)")
    msg_parser.add_argument("--max-pages", type=int, default=None, help="Maximum pages to fetch with --all-pages (default: 50)")
    cal_parser = subparsers.add_parser("calendar", parents=[_global_flags], help="Fetch calendar events by date range")
    cal_parser.add_argument("--child", help="Filter by child name (case-insensitive substring match)")
    cal_parser.add_argument("--since", help="Start date (YYYY-MM-DD, inclusive). Default: today.")
    cal_parser.add_argument("--until", help="End date (YYYY-MM-DD, inclusive). Default: 30 days from today; maximum 30-day difference (31 dates).")
    att_parser = subparsers.add_parser("attendance", parents=[_global_flags], help="Fetch an attendance / leave request by ID")
    att_parser.add_argument("id", help="Attendance/leave request ID (from notifications output)")
    att_parser.add_argument("--child", help="Switch to this child's context before fetching")
    absence_parser = subparsers.add_parser("absence", parents=[_global_flags],
        help="Preview or report a full school day absent (today or tomorrow)")
    absence_parser.add_argument("--child", required=True, help="Exact or unique child name")
    absence_parser.add_argument("--date", required=True, help="Local date, YYYY-MM-DD; today or tomorrow only")
    absence_parser.add_argument("--apply", action="store_true", help="Submit the absence report")
    absence_parser.add_argument("--confirm", action="store_true", help="Required with --apply")
    leave_parser = subparsers.add_parser("leave", parents=[_global_flags],
        help="Preview or create a future school leave application")
    leave_parser.add_argument("--child", required=True, help="Exact or unique child name")
    leave_parser.add_argument("--from", dest="from_date", required=True, help="First date, YYYY-MM-DD")
    leave_parser.add_argument("--until", required=True, help="Last date, YYYY-MM-DD")
    leave_parser.add_argument("--reason", required=True, help="Reason sent to the school")
    leave_parser.add_argument("--start", help="Optional start time, HH:MM")
    leave_parser.add_argument("--end", help="Optional end time, HH:MM")
    leave_parser.add_argument("--apply", action="store_true", help="Submit the application")
    leave_parser.add_argument("--confirm", action="store_true", help="Required with --apply")
    fritids_parser = subparsers.add_parser("fritids", parents=[_global_flags],
        help="Preview or set one day's fritids schedule")
    fritids_parser.add_argument("--child", required=True, help="Exact or unique child name")
    fritids_parser.add_argument("--date", required=True, help="Local date, YYYY-MM-DD")
    fritids_choice = fritids_parser.add_mutually_exclusive_group(required=True)
    fritids_choice.add_argument("--free", action="store_true", help="Mark the child free from fritids")
    fritids_choice.add_argument("--start", help="Arrival time, HH:MM; requires --end")
    fritids_parser.add_argument("--end", help="Departure time, HH:MM; requires --start")
    fritids_parser.add_argument("--overwrite-existing", action="store_true",
        help="Required when changing an existing schedule")
    fritids_parser.add_argument("--apply", action="store_true", help="Save the fritids day")
    fritids_parser.add_argument("--confirm", action="store_true", help="Required with --apply")
    news_parser = subparsers.add_parser("news", parents=[_global_flags], help="Fetch a news item by ID")
    news_parser.add_argument("id", help="News item ID (from notifications output)")
    news_parser.add_argument("--child", help="Switch to this child's context before fetching")
    meeting_parser = subparsers.add_parser("meeting", parents=[_global_flags], help="Fetch meeting slot availabilities for a child")
    meeting_parser.add_argument("--child", help="Switch to this child's context before fetching")
    pickup_parser = subparsers.add_parser("comment", parents=[_global_flags],
        help="Read, preview, or apply a time registration comment for one date",
        epilog="""examples:
  deformentor comment --child CHILD_NAME --date 2026-06-05
  deformentor comment --child CHILD_NAME --date 2026-06-06 --comment \"COMMENT_TEXT\"
  deformentor comment --child CHILD_NAME --date 2026-06-05 --comment \"COMMENT_TEXT\" --apply --confirm

safety:
  default is preview/read-only; --apply --confirm is required to write
  --child must be an exact or unique match
  --comment is kept exactly as provided; whitespace is preserved
  --overwrite-existing is required to replace another non-empty comment
  --destination-log is appended only after a verified write and is chmod 0600
  plain filenames such as destination.log are supported""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    pickup_parser.add_argument("--child", required=True, help="Switch to this child's context; exact or unique match required")
    pickup_parser.add_argument("--date", required=True, help="Comment date in exact YYYY-MM-DD format")
    pickup_parser.add_argument("--comment", help="Exact comment text to preview or write; whitespace is preserved")
    pickup_parser.add_argument("--apply", action="store_true", help="Actually write the comment after all safety checks pass")
    pickup_parser.add_argument("--confirm", action="store_true", help="Required together with --apply to allow writing")
    pickup_parser.add_argument("--overwrite-existing", action="store_true", help="Required when replacing an existing non-empty comment")
    pickup_parser.add_argument("--destination-log", help="Optional JSONL log path; appended after verified write and chmod 0600")
    att2_parser = subparsers.add_parser("attachment", parents=[_global_flags],
        help="Fetch an attachment and write bytes to stdout",
        epilog="""examples:
  deformentor attachment --url "/Resources/Resource/Download/123?api=IM2" > doc.pdf""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    att2_parser.add_argument("--url", required=True, help="Attachment URL path (from news detail attachments[].url)")
    att2_parser.add_argument("--child", help="Switch to this child's context before fetching")
    status_parser = subparsers.add_parser("status", parents=[_global_flags], help="Show configuration and session status")
    status_parser.add_argument("--json", dest="json_output", action="store_true", help="Output status as JSON to stdout")
    subparsers.add_parser("reset", parents=[_base_flags], help="Remove configuration, session, and OAuth credentials")

    if _HAS_ARGCOMPLETE:
        argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if args.command is None:
        _maybe_print_logo()
        parser.print_help(sys.stdout)
        sys.exit(0)

    try:
        _validate_fields_usage(args)
        if getattr(args, "debug", False):
            _configure_debug()
        if args.command == "setup":
            _setup(
                quiet=args.quiet,
                no_input=getattr(args, "no_input", False),
                personnummer=getattr(args, "personnummer", None),
            )
        elif args.command == "notifications":
            _notifications(args)
        elif args.command == "messages":
            _messages(args)
        elif args.command == "calendar":
            _calendar(args)
        elif args.command == "attendance":
            _attendance(args)
        elif args.command == "absence":
            _absence(args)
        elif args.command == "leave":
            _leave(args)
        elif args.command == "fritids":
            _fritids(args)
        elif args.command == "news":
            _news(args)
        elif args.command == "meeting":
            _meeting(args)
        elif args.command == "comment":
            _pickup_comment(args)
        elif args.command == "attachment":
            _attachment(args)
        elif args.command == "status":
            _status(args)
        elif args.command == "reset":
            _reset(args)
    except BrokenPipeError:
        _exit_broken_pipe()
    except KeyboardInterrupt:
        emit_error("interrupted", "Interrupted by user.", exit_code=130)
    except OAuthSetupRequired:
        emit_error(
            "oauth_setup_required",
            "OAuth authentication must be renewed. Run `deformentor setup` and approve Freja; your stored personnummer will be reused.",
            exit_code=EXIT_AUTH,
        )
    except AuthenticationError:
        emit_error("auth_failed", "InfoMentor authentication failed.", exit_code=EXIT_AUTH)
    except FrejaHttpError as error:
        status_code = getattr(error, "status_code", None)
        if status_code is not None and status_code >= 500:
            emit_error("server_error", f"Freja returned HTTP status {status_code}.", exit_code=EXIT_NETWORK)
        emit_error("auth_failed", "Freja authentication failed.", exit_code=EXIT_AUTH)
    except FrejaError:
        emit_error("auth_failed", "Freja authentication failed.", exit_code=EXIT_AUTH)
    except UpstreamStateError as e:
        emit_error("upstream_state_error", str(e), exit_code=EXIT_NETWORK)
    except requests.HTTPError as e:
        status_code = getattr(e.response, "status_code", None)
        if status_code in {401, 403}:
            emit_error("auth_failed", f"Authentication failed with HTTP status {status_code}.", exit_code=EXIT_AUTH)
        if status_code == 404:
            emit_error("not_found", "The requested resource was not found.", exit_code=EXIT_NOT_FOUND)
        if status_code is not None and status_code >= 500:
            emit_error("server_error", f"InfoMentor returned HTTP status {status_code}.", exit_code=EXIT_NETWORK)
        message = f"Request failed with HTTP status {status_code}." if status_code is not None else "HTTP request failed."
        emit_error("http_error", message, exit_code=EXIT_ERROR)
    except requests.Timeout:
        emit_error("request_timeout", "Request timed out.", exit_code=EXIT_NETWORK)
    except requests.ConnectionError:
        emit_error("connection_failed", "Connection failed. Check your network.", exit_code=EXIT_NETWORK)
    except requests.RequestException:
        emit_error("request_failed", "Request failed.", exit_code=EXIT_NETWORK)
    except Exception:
        emit_error("internal_error", "Unexpected internal error.", exit_code=EXIT_ERROR)


def _setup(quiet=False, no_input=False, personnummer=None):
    if not no_input and sys.stdin.isatty():
        _maybe_print_logo()
    existing = dotenv_values(CONFIG_FILE).get("PERSONNUMMER") if CONFIG_FILE.exists() else None

    if personnummer is not None:
        _validate_personnummer(personnummer)
    elif existing:
        _validate_personnummer(existing, stored=True)
        personnummer = existing
    elif no_input or not sys.stdin.isatty():
        personnummer = os.environ.get("PERSONNUMMER")
        if not personnummer:
            emit_error(
                "setup_required",
                "PERSONNUMMER env var required for initial non-interactive setup.",
                exit_code=EXIT_USAGE,
            )
        _validate_personnummer(personnummer)
    else:
        personnummer = input("Personnummer (12 digits): ").strip()
        _validate_personnummer(personnummer)

    session, oauth_credential = setup_login(personnummer)
    _persist_setup_state(personnummer, session, oauth_credential, quiet)
    _progress("Authenticated.", quiet)
    _print_status(_get_status())


def _get_session(quiet=False):
    """Authenticate and return a session. Exits if not configured."""
    config = dotenv_values(CONFIG_FILE)
    personnummer = config.get("PERSONNUMMER")
    if not personnummer:
        emit_error("not_configured", "PERSONNUMMER not set. Run: deformentor setup", exit_code=EXIT_AUTH)
    _validate_personnummer(personnummer, stored=True)
    return login(
        session_path=str(SESSION_FILE),
        oauth_path=str(OAUTH_FILE),
        lock_path=str(OAUTH_LOCK_FILE),
    )


def _append_destination_log(path, entry):
    """Append a JSONL destination log entry with private file permissions."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fd = None
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        if fd is not None:
            os.close(fd)


def _pickup_comment(args):
    pickup_date = _validate_exact_pickup_date(args.date)
    proposed_comment = _validate_comment_text(args.comment) if args.comment is not None else None
    if args.apply and proposed_comment is None:
        emit_error("invalid_input", "--apply requires --comment.", exit_code=EXIT_USAGE)
    if args.apply and not args.confirm:
        emit_error("invalid_input", "--apply requires --confirm.", exit_code=EXIT_USAGE)

    session = _get_session(quiet=args.quiet)
    _resolve_and_switch_child(session, args.child)
    registration = None

    try:
        registration = get_time_registration_for_date(session, pickup_date)
    except RuntimeError as exc:
        emit_error("not_found", f"No time registration found for child '{args.child}' on {pickup_date}: {exc}", exit_code=EXIT_NOT_FOUND)

    raw_comments = get_time_registration_comments(session, pickup_date)
    existing_comment = normalize_time_registration_comment(raw_comments)

    has_existing = existing_comment.get("found", False)
    existing_text = existing_comment.get("userComment") if has_existing else None
    existing_differs = bool(proposed_comment and has_existing and existing_text != proposed_comment)
    blocked = existing_differs

    result = {
        "child": args.child,
        "child_id": registration.get("pupilId") or registration.get("childId"),
        "date": pickup_date,
        "date_input": args.date,
        "mode": "apply" if args.apply else "preview",
        "time_registration": {
            "timeRegistrationId": registration.get("timeRegistrationId"),
            "found": True,
        },
        "existing_comment": existing_comment,
        "proposed_comment": proposed_comment,
        "write_performed": False,
        "would_write_if_applied": bool(proposed_comment and not existing_differs),
        "blocked": blocked,
    }
    if blocked:
        result["block_reason"] = "existing_comment_requires_overwrite_confirmation"

    if not args.apply:
        _output_json(result, args)
        return

    if proposed_comment is None:
        emit_error("invalid_input", "--apply requires --comment.", exit_code=EXIT_USAGE)
    if not args.confirm:
        emit_error("invalid_input", "--apply requires --confirm.", exit_code=EXIT_USAGE)
    if existing_differs and not args.overwrite_existing:
        emit_error("invalid_input", "Existing comment differs; rerun with --overwrite-existing to replace it.", exit_code=EXIT_USAGE)

    if has_existing and existing_text == proposed_comment:
        result["verified"] = True
        result["blocked"] = False
        result["would_write_if_applied"] = False
        _output_json(result, args)
        return

    comment_id = existing_comment.get("parentCommentId", 0) if has_existing else 0
    save_time_registration_comment(session, comment_id, proposed_comment, registration.get("timeRegistrationId"))
    verified_raw_comments = get_time_registration_comments(session, pickup_date)
    verified_comment = normalize_time_registration_comment(verified_raw_comments)
    if not verified_comment.get("found") or verified_comment.get("userComment") != proposed_comment:
        emit_error("verification_failed", f"Comment save could not be verified for {pickup_date}.", exit_code=EXIT_ERROR)

    result.update({
        "existing_comment": verified_comment,
        "write_performed": True,
        "would_write_if_applied": False,
        "blocked": False,
        "verified": True,
        "written_comment": proposed_comment,
        "previous_comment": existing_text,
        "previous_owner": existing_comment.get("owner") if has_existing else None,
    })
    result.pop("block_reason", None)

    if args.destination_log:
        log_entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "destination": "informentor",
            "action": "time_registration_comment_saved",
            "child": args.child,
            "child_id": result.get("child_id"),
            "date": pickup_date,
            "comment": proposed_comment,
            "previous_comment": existing_text,
            "previous_owner": existing_comment.get("owner") if has_existing else None,
            "verified": True,
            "tool": "deformentor-cli",
        }
        try:
            _append_destination_log(args.destination_log, log_entry)
        except OSError as exc:
            emit_error("destination_log_failed", f"InfoMentor write was verified, but destination log append failed: {exc}", exit_code=EXIT_ERROR)
        result["destination_log"] = {"written": True, "path": args.destination_log}

    _output_json(result, args)


def _notifications(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    if since and until and since > until:
        emit_error("invalid_input", "--since cannot be after --until.", exit_code=EXIT_USAGE)
    if args.type and args.type.lower() not in KNOWN_NOTIFICATION_TYPES:
        emit_error(
            "invalid_input",
            f"Unknown notification type '{args.type}'. Known types: {', '.join(sorted(KNOWN_NOTIFICATION_TYPES))}",
            exit_code=EXIT_USAGE,
        )
    session = _get_session(quiet=args.quiet)
    _progress("Fetching notifications...", args.quiet)
    result = fetch_all_notifications(session)
    result = _filter_children(result, args.child)
    if args.child and not result:
        emit_error("child_not_found", f"No child matching '{args.child}'.", exit_code=EXIT_NOT_FOUND)
    for entry in result:
        entry["notifications"] = _filter_items_by_type(entry["notifications"], args.type)
        entry["notifications"] = _filter_items_since(entry["notifications"], since)
        entry["notifications"] = _filter_items_until(entry["notifications"], until)
    _output_json(result, args)


def _messages(args):
    config = dotenv_values(CONFIG_FILE)
    since = _resolve_since(args.since, config)
    until = _resolve_until(getattr(args, "until", None))
    if since and until and since > until:
        emit_error("invalid_input", "--since cannot be after --until.", exit_code=EXIT_USAGE)
    fetch_all_pages = getattr(args, "all_pages", False) is True
    raw_max_pages = getattr(args, "max_pages", None)
    if not isinstance(raw_max_pages, int):
        raw_max_pages = None
    if raw_max_pages is not None and not fetch_all_pages:
        emit_error("invalid_input", "--max-pages requires --all-pages.", exit_code=EXIT_USAGE)
    if raw_max_pages is not None and raw_max_pages <= 0:
        emit_error("invalid_input", "--max-pages must be a positive integer.", exit_code=EXIT_USAGE)
    max_pages = raw_max_pages if raw_max_pages is not None else 50
    session = _get_session(quiet=args.quiet)
    _progress("Fetching messages...", args.quiet)
    result = fetch_all_messages(session, fetch_all_pages=fetch_all_pages, max_pages=max_pages)
    result = _filter_children(result, args.child)
    if args.child and not result:
        emit_error("child_not_found", f"No child matching '{args.child}'.", exit_code=EXIT_NOT_FOUND)
    for entry in result:
        entry["messages"] = _filter_items_since(entry["messages"], since)
        entry["messages"] = _filter_items_until(entry["messages"], until)
    _output_json(result, args)


def _calendar(args):
    since, until = _resolve_calendar_range(args.since, args.until)
    session = _get_session(quiet=args.quiet)
    children = get_children(session)
    if args.child:
        child_query = args.child.lower()
        children = [child for child in children if child_query in child["name"].lower()]
        if not children:
            emit_error("child_not_found", f"No child matching '{args.child}'.", exit_code=EXIT_NOT_FOUND)
    _progress("Fetching calendar events...", args.quiet)
    result = fetch_all_calendar_events(
        session, since, until, children, allow_partial=True,
    )
    incomplete = [entry for entry in result if entry["status"] != "complete"]
    if incomplete:
        successful = [entry for entry in result if entry["status"] != "unavailable"]
        if successful:
            _output_json(result, args)
            emit_error(
                "calendar_incomplete",
                "Calendar data is incomplete for one or more children or events.",
                exit_code=EXIT_NETWORK,
            )
        emit_error(
            "calendar_unavailable",
            "InfoMentor could not return calendar entries for the selected children.",
            exit_code=EXIT_NETWORK,
        )
    _output_json(result, args)


def _attendance(args):
    _validate_positive_decimal_id(args.id, "Attendance request ID")
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching attendance detail...", args.quiet)
    result = get_attendance_detail(session, args.id)
    _output_json(result, args)


def _absence(args):
    target = _validate_exact_date(args.date, "--date")
    if target not in (date.today(), date.today() + timedelta(days=1)):
        emit_error("invalid_input", "School absence can only be reported for today or tomorrow.", exit_code=EXIT_USAGE)
    _require_write_flags(args)
    session = _get_session(quiet=args.quiet)
    child = _resolve_and_switch_child(session, args.child)
    data = get_attendance_app_data(session)
    day = next((name for name in ("today", "tomorrow")
                if data.get(f"absence{name.title()}Date") == target.isoformat()), None)
    if day is None or data.get("canRegisterDayAbsence") is not True:
        emit_error("absence_unavailable", "Full-day absence reporting is unavailable for this child and date.", exit_code=EXIT_USAGE)
    if not isinstance(data.get(f"absent{day.title()}"), bool) or not isinstance(data.get(f"absence{day.title()}Locked"), bool):
        raise UpstreamStateError("InfoMentor returned invalid absence status.")
    absent = data.get(f"absent{day.title()}") is True
    locked = data.get(f"absence{day.title()}Locked") is True
    result = {
        "child": child["name"], "child_id": child["id"], "date": target.isoformat(),
        "mode": "apply" if args.apply else "preview", "already_absent": absent,
        "locked": locked, "would_write_if_applied": not absent and not locked,
        "write_performed": False,
    }
    if not args.apply:
        _output_json(result, args)
        return
    if absent:
        result["verified"] = True
        _output_json(result, args)
        return
    if locked:
        emit_error("absence_locked", "InfoMentor has locked absence reporting for this date.", exit_code=EXIT_USAGE)
    report_full_day_absence(session, day, False)
    saved = get_attendance_app_data(session)
    if saved.get(f"absent{day.title()}") is not True:
        emit_error("verification_failed", "The school absence report could not be verified.", exit_code=EXIT_ERROR)
    result.update(write_performed=True, verified=True, already_absent=True, would_write_if_applied=False)
    _output_json(result, args)


def _leave(args):
    start_date = _validate_exact_date(args.from_date, "--from")
    end_date = _validate_exact_date(args.until, "--until")
    if start_date < date.today() + timedelta(days=2):
        emit_error("invalid_input", "Leave applications must start at least two days from today.", exit_code=EXIT_USAGE)
    if end_date < start_date:
        emit_error("invalid_input", "--until cannot be before --from.", exit_code=EXIT_USAGE)
    if not args.reason.strip():
        emit_error("invalid_input", "--reason must not be empty.", exit_code=EXIT_USAGE)
    if bool(args.start) != bool(args.end):
        emit_error("invalid_input", "--start and --end must be provided together.", exit_code=EXIT_USAGE)
    start_time = _validate_time(args.start, "--start") if args.start else None
    end_time = _validate_time(args.end, "--end") if args.end else None
    if start_time and (int(start_time[-2:]) % 15 or int(end_time[-2:]) % 15):
        emit_error("invalid_input", "Leave times must be on 15-minute boundaries.", exit_code=EXIT_USAGE)
    if start_date == end_date and start_time and start_time >= end_time:
        emit_error("invalid_input", "--end must be after --start on a single day.", exit_code=EXIT_USAGE)
    _require_write_flags(args)
    session = _get_session(quiet=args.quiet)
    child = _resolve_and_switch_child(session, args.child)
    data = get_attendance_app_data(session)
    if data.get("canRequestLeave") is not True:
        emit_error("leave_unavailable", "Leave applications are unavailable for this child.", exit_code=EXIT_USAGE)
    requests_list = get_leave_requests(session)
    overlaps = []
    for item in requests_list:
        if not isinstance(item, dict) or not isinstance(item.get("fromDate"), str) or not isinstance(item.get("toDate"), str):
            raise UpstreamStateError("InfoMentor returned invalid leave request dates.")
        try:
            date.fromisoformat(item["fromDate"][:10])
            date.fromisoformat(item["toDate"][:10])
        except ValueError:
            raise UpstreamStateError("InfoMentor returned invalid leave request dates.") from None
        if item.get("status") not in {"Denied", "Expired"} and item["fromDate"][:10] <= end_date.isoformat() and item["toDate"][:10] >= start_date.isoformat():
            overlaps.append({"id": item.get("id"), "status": item.get("status"),
                             "from_date": item["fromDate"][:10], "until": item["toDate"][:10]})
    result = {
        "child": child["name"], "child_id": child["id"],
        "from_date": start_date.isoformat(), "until": end_date.isoformat(),
        "start": start_time, "end": end_time, "reason": args.reason,
        "mode": "apply" if args.apply else "preview", "overlapping_requests": overlaps,
        "would_write_if_applied": not overlaps, "write_performed": False,
    }
    if not args.apply:
        _output_json(result, args)
        return
    if overlaps:
        emit_error("overlapping_leave", "An existing leave application overlaps this date range.", exit_code=EXIT_USAGE)
    request_id = create_leave_request(session, start_date.isoformat(), end_date.isoformat(),
                                      args.reason, start_time, end_time)
    saved = next((item for item in get_leave_requests(session)
                  if isinstance(item, dict) and str(item.get("id")) == str(request_id)), None)
    if (not saved or saved.get("fromDate", "")[:10] != start_date.isoformat()
            or saved.get("toDate", "")[:10] != end_date.isoformat()
            or saved.get("requesterComment") != args.reason):
        emit_error("verification_failed", "The leave application could not be verified.", exit_code=EXIT_ERROR)
    result.update(id=request_id, status=saved.get("status"), write_performed=True,
                  verified=True, would_write_if_applied=False)
    _output_json(result, args)


def _fritids(args):
    target = _validate_exact_date(args.date, "--date")
    if target < date.today():
        emit_error("invalid_input", "Fritids schedule date cannot be in the past.", exit_code=EXIT_USAGE)
    if args.free and args.end or not args.free and not args.end:
        emit_error("invalid_input", "Use --free or both --start and --end.", exit_code=EXIT_USAGE)
    start_time = _validate_time(args.start, "--start") if args.start else None
    end_time = _validate_time(args.end, "--end") if args.end else None
    if start_time and start_time >= end_time:
        emit_error("invalid_input", "--end must be after --start.", exit_code=EXIT_USAGE)
    _require_write_flags(args)
    session = _get_session(quiet=args.quiet)
    child = _resolve_and_switch_child(session, args.child)
    try:
        row = get_time_registration_for_date(session, target.isoformat())
    except RuntimeError:
        emit_error("not_found", "No fritids schedule day was found for this date.", exit_code=EXIT_NOT_FOUND)
    if not isinstance(row, dict) or str(row.get("date", ""))[:10] != target.isoformat():
        raise UpstreamStateError("InfoMentor returned invalid fritids schedule data.")
    if not all(isinstance(row.get(key), bool) for key in ("onLeave", "canEdit", "isLocked")):
        raise UpstreamStateError("InfoMentor returned invalid fritids schedule status.")
    for key, value in (("schoolOpeningTime", start_time), ("schoolClosingTime", end_time)):
        bound = row.get(key)
        if value and isinstance(bound, str) and (value < bound[11:16] if key == "schoolOpeningTime" else value > bound[11:16]):
            emit_error("invalid_input", "Fritids times must be within the school's opening hours.", exit_code=EXIT_USAGE)
    existing_free = row.get("onLeave") is True
    existing_start = str(row.get("startDateTime") or "")[11:16] or None
    existing_end = str(row.get("endDateTime") or "")[11:16] or None
    same = (existing_free if args.free else not existing_free and existing_start == start_time and existing_end == end_time)
    has_existing = existing_free or bool(existing_start or existing_end)
    editable = row.get("canEdit") is True and row.get("isLocked") is not True
    needs_overwrite = has_existing and not same
    result = {
        "child": child["name"], "child_id": child["id"], "date": target.isoformat(),
        "mode": "apply" if args.apply else "preview",
        "existing": {"free": existing_free, "start": existing_start, "end": existing_end},
        "proposed": {"free": args.free, "start": start_time, "end": end_time},
        "editable": editable, "overwrite_required": needs_overwrite,
        "would_write_if_applied": editable and not same and (not needs_overwrite or args.overwrite_existing),
        "write_performed": False,
    }
    if not args.apply:
        _output_json(result, args)
        return
    if same:
        result["verified"] = True
        _output_json(result, args)
        return
    if not editable:
        emit_error("fritids_locked", "This fritids schedule day cannot be edited.", exit_code=EXIT_USAGE)
    if needs_overwrite and not args.overwrite_existing:
        emit_error("existing_schedule", "Use --overwrite-existing to change this fritids schedule day.", exit_code=EXIT_USAGE)
    save_time_registration_day(session, row, free=args.free, start=start_time, end=end_time)
    try:
        saved = get_time_registration_for_date(session, target.isoformat())
    except RuntimeError:
        saved = None
    if (not isinstance(saved, dict) or
            (saved.get("onLeave") is True) != args.free or
            (not args.free and (str(saved.get("startDateTime") or "")[11:16] != start_time or
                                str(saved.get("endDateTime") or "")[11:16] != end_time))):
        emit_error("verification_failed", "The fritids schedule could not be verified.", exit_code=EXIT_ERROR)
    result.update(write_performed=True, verified=True, would_write_if_applied=False)
    _output_json(result, args)


def _news(args):
    _validate_positive_decimal_id(args.id, "News item ID")
    session = _get_session(quiet=args.quiet)
    if args.child:
        _resolve_and_switch_child(session, args.child)
    _progress("Fetching news item...", args.quiet)
    result = get_news_detail(session, args.id)
    if result is None:
        if args.child:
            emit_error("not_found", f"News item {args.id} not found under child '{args.child}'.", exit_code=EXIT_NOT_FOUND)
        else:
            emit_error("not_found", f"News item {args.id} not found. If this item belongs to a specific child, retry with --child <name>.", exit_code=EXIT_NOT_FOUND)
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
    try:
        validate_attachment_url(args.url)
    except ValueError as e:
        emit_error("invalid_input", str(e), exit_code=EXIT_USAGE)
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


def _reset(args):
    """Remove configuration, session cookies, and OAuth credentials."""
    deleted = []
    failed = []
    paths = [CONFIG_FILE, SESSION_FILE, OAUTH_FILE]

    def delete_state():
        for path in paths:
            if path.exists():
                try:
                    path.unlink()
                    deleted.append(str(path))
                except OSError:
                    failed.append(str(path))

    try:
        if OAUTH_FILE.exists() or OAUTH_LOCK_FILE.exists():
            OAUTH_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with portalocker.Lock(str(OAUTH_LOCK_FILE), mode="a", timeout=HTTP_TIMEOUT):
                delete_state()
        else:
            delete_state()
    except portalocker.exceptions.LockException:
        emit_error(
            "reset_failed",
            "Could not reset authentication while another Deformentor process is using it.",
            exit_code=EXIT_ERROR,
        )
    if failed:
        emit_error(
            "reset_failed",
            f"Could not delete {len(failed)} local state file(s).",
            exit_code=EXIT_ERROR,
        )
    if not args.quiet and deleted:
        for p in deleted:
            print(f"Deleted {p}", file=sys.stderr)
    if not args.quiet and not deleted and not failed:
        print("Nothing to reset - no config or session files found.", file=sys.stderr)
    print(json.dumps({"reset": True, "deleted": deleted, "failed": failed}))


if __name__ == "__main__":
    main()
