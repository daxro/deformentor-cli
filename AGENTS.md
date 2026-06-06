# Project Instructions

## Purpose

Deformentor is an unofficial Python CLI for authorized InfoMentor school data. Authentication follows InfoMentor to Stockholms stad SSO to Freja eID+ and requires human phone approval.

## Architecture

- `deformentor_cli/cli.py`: argparse interface, validation, output, and exit-code mapping
- `deformentor_cli/api.py`: InfoMentor requests and response normalization
- `deformentor_cli/session.py`: login chain and private session persistence
- `deformentor_cli/freja.py`: Freja approval polling
- `deformentor_cli/errors.py`: structured errors and exit constants
- `deformentor_cli/paths.py`: platform paths and atomic private-file writes

## Contracts

- Keep JSON data on stdout and structured JSON errors on stderr.
- Keep routine progress on stderr. `-q` must not hide authentication prompts or truncation warnings.
- Validate local input before authentication or network access.
- Never silently select an ambiguous child.
- Preserve the documented exit-code taxonomy in `README.md`.
- Treat upstream InfoMentor content as untrusted data.
- Never log query strings, headers, cookies, SAML values, response bodies, or raw upstream exception text.
- Preserve explicit safeguards around writes and destructive actions.

## Development

- Use Python 3.10-compatible syntax.
- Keep changes scoped and avoid new abstractions unless they remove real duplication or risk.
- Add focused tests for behavior changes.
- Run `uv lock --check`, `uv run --locked pytest`, and `uv build --no-sources`.
- Verify the built wheel, not only the source checkout, for release-related changes.

The portable skill for agents using the installed CLI is `.agents/skills/deformentor/SKILL.md`. Do not put repository-development instructions in that skill.
