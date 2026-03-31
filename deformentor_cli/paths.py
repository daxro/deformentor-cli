"""Platform-native paths for config and state files (via platformdirs)."""

from pathlib import Path

import platformdirs

_APP = "deformentor"

CONFIG_DIR = Path(platformdirs.user_config_dir(_APP))
STATE_DIR = Path(platformdirs.user_state_dir(_APP))

CONFIG_FILE = CONFIG_DIR / "config.env"
SESSION_FILE = STATE_DIR / "session.json"
