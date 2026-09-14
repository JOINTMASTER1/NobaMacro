"""Keep a complete helper runtime for the lifetime of the open application."""
from pathlib import Path
import tempfile


HELPER_FILES = ("backend.py", "macro_core.py", "pointer_control.py", "kde_pointer.py", "app_settings.py", "kde_shortcuts.py")


class RuntimeFiles:
    def __init__(self, source_directory):
        source_directory = Path(source_directory)
        contents = {}
        # Validate the complete installation before creating a partial runtime.
        for name in HELPER_FILES:
            source = source_directory / name
            try:
                contents[name] = source.read_bytes()
            except OSError as exc:
                raise RuntimeError(
                    f"Cannot read the application file:\n{source}\n\n"
                    "Close NobaMacro, extract the complete ZIP into a permanent folder, "
                    "and run launch.sh from that folder. Do not copy only some Python files. "
                    "Your recording has not been replaced."
                ) from exc
        self.temp = tempfile.TemporaryDirectory(prefix="nobamacro-session-")
        self.directory = Path(self.temp.name)
        try:
            for name, content in contents.items():
                (self.directory / name).write_bytes(content)
        except Exception:
            self.temp.cleanup()
            raise

    def path(self, name):
        if name not in HELPER_FILES:
            raise ValueError("Unknown runtime helper")
        path = self.directory / name
        if not path.is_file():
            raise RuntimeError("The running app's temporary files were removed. Save your recording and restart NobaMacro.")
        return path

    def close(self):
        self.temp.cleanup()
