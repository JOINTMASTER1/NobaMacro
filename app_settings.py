"""Validated, persistent user preferences and portable function-key mappings."""
import json
import os
from pathlib import Path
import tempfile

FKEY_CODES = {**{f"F{i}": 58 + i for i in range(1, 11)}, "F11": 87, "F12": 88}
DEFAULT_BINDINGS = {"record": "F8", "stop": "F9", "play": "F10"}


def validate_bindings(bindings):
    if not isinstance(bindings, dict) or set(bindings) != set(DEFAULT_BINDINGS):
        raise ValueError("Choose a Record/Stop, Stop and Play key")
    if any(not isinstance(key, str) or key not in FKEY_CODES for key in bindings.values()):
        raise ValueError("Shortcuts must be function keys F1–F12")
    if len(set(bindings.values())) != 3:
        raise ValueError("Choose a different function key for each action")
    if bindings["record"] == "F9" or bindings["play"] == "F9":
        raise ValueError("F9 is reserved for emergency Stop")
    return dict(bindings)


def control_codes(bindings):
    return {67, *(FKEY_CODES[key] for key in validate_bindings(bindings).values())}


def settings_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "NobaMacro" / "settings.json"


def load_settings(path=None):
    path = Path(path) if path is not None else settings_path()
    if not path.exists():
        return {"bindings": dict(DEFAULT_BINDINGS), "global_shortcuts": True}
    if path.stat().st_size > 16_384:
        raise ValueError("Settings file is too large")
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or type(settings.get("global_shortcuts")) is not bool:
        raise ValueError("Invalid shortcut settings")
    return {"bindings": validate_bindings(settings.get("bindings")),
            "global_shortcuts": settings["global_shortcuts"]}


def save_settings(settings, path=None):
    values = {"bindings": validate_bindings(settings.get("bindings")),
              "global_shortcuts": settings.get("global_shortcuts")}
    if type(values["global_shortcuts"]) is not bool:
        raise ValueError("Global shortcuts must be on or off")
    path = Path(path) if path is not None else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".settings-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(values, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
