"""Lets the tests import app.py without a real config.ini."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_boot_config = Path(tempfile.mkdtemp()) / "config.ini"
_boot_config.write_text("[server]\nport = 8080\n")
os.environ["PICLOCK_CONFIG"] = str(_boot_config)
os.environ["PICLOCK_KEY_FILE"] = str(_boot_config.parent / "secret.key")   # never the real key


@pytest.fixture
def app_module(tmp_path):
    """The app module with a fresh, empty config. Use set_config() to change it."""
    import app

    def set_config(text):
        path = tmp_path / "config.ini"
        path.write_text(text)
        app.cfg = app.load_config(path)
        return app.cfg

    set_config("[server]\nport = 8080\n")
    app.set_config = set_config
    app.POLLERS.clear()
    return app
