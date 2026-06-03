"""Load Azure Functions `local.settings.json` Values into os.environ for local runs.

On Azure these are real app settings; locally we mirror them into the environment so
the same `os.environ[...]` lookups work in both places.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "local.settings.json"


def load_local_settings(path=DEFAULT_PATH) -> None:
    path = Path(path)
    if not path.exists():
        return
    values = json.loads(path.read_text(encoding="utf-8-sig")).get("Values", {})
    for key, value in values.items():
        os.environ.setdefault(key, str(value))
