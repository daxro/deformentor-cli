"""Platform-native paths for config and state files (via platformdirs)."""

import os
import tempfile
from pathlib import Path

import platformdirs

_APP = "deformentor"

CONFIG_DIR = Path(platformdirs.user_config_dir(_APP))
STATE_DIR = Path(platformdirs.user_state_dir(_APP))

CONFIG_FILE = CONFIG_DIR / "config.env"
SESSION_FILE = STATE_DIR / "session.json"
OAUTH_FILE = STATE_DIR / "oauth.json"
OAUTH_LOCK_FILE = STATE_DIR / "oauth.lock"


def write_private_text(path, content):
    """Atomically replace a text file while keeping it owner-only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
