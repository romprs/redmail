from __future__ import annotations

import os
import sys
from pathlib import Path


def app_dir() -> Path:
    """Каталог для настроек и кэша: %APPDATA%\\redmail на Windows, ~/.config/redmail на Linux."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "redmail"
