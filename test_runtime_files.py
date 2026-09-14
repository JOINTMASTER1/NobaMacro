"""Regression tests for missing installation files on a second operation."""
from pathlib import Path
import tempfile
import types
import threading
import unittest
from unittest.mock import Mock, patch

from runtime_files import HELPER_FILES, RuntimeFiles
from nobamacro import App
from test_nobamacro import sample
from app_settings import DEFAULT_BINDINGS


class RuntimeFilesTests(unittest.TestCase):
    def make_installation(self, root):
        source = Path(root) / "installed"
        source.mkdir()
        for name in HELPER_FILES:
            (source / name).write_text(f"# {name}\n", encoding="utf-8")
        return source

    def test_helpers_survive_installation_folder_rename(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_installation(root)
            runtime = RuntimeFiles(source)
            try:
                original_path = runtime.path("kde_pointer.py")
                moved = Path(root) / "renamed"
                # Both paths are inside this test's own temporary directory.
                assert source.resolve().is_relative_to(Path(root).resolve())
                assert moved.resolve().is_relative_to(Path(root).resolve())
                source.rename(moved)
                for _ in range(3):
                    self.assertEqual(runtime.path("kde_pointer.py"), original_path)
                    self.assertEqual(original_path.read_text(), "# kde_pointer.py\n")
                    self.assertTrue(runtime.path("backend.py").is_file())
            finally:
                runtime.close()
            self.assertFalse(original_path.exists())

    def test_helpers_survive_original_file_removal(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_installation(root)
            runtime = RuntimeFiles(source)
            try:
                (source / "kde_pointer.py").unlink()
                self.assertTrue(runtime.path("kde_pointer.py").is_file())
                self.assertTrue(runtime.path("macro_core.py").is_file())
            finally:
                runtime.close()

    def test_missing_file_at_start_has_recovery_instructions(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.make_installation(root)
            (source / "kde_pointer.py").unlink()
            with self.assertRaisesRegex(RuntimeError, "kde_pointer.py") as raised:
                RuntimeFiles(source)
            self.assertIn("extract the complete ZIP", str(raised.exception))
            self.assertIn("has not been replaced", str(raised.exception))

    def test_record_play_record_reuses_runtime_until_app_close(self):
        app = App.__new__(App)
        app.process = None
        app.busy = threading.Event()
        app.settings = {"bindings": dict(DEFAULT_BINDINGS), "global_shortcuts": False}
        app.root = Mock()
        app.runtime = Mock()
        runtime = app.runtime
        runtime.path.side_effect = lambda name: Path("session-runtime") / name
        app.auto_position = types.SimpleNamespace(get=lambda: True)
        for key, value in (("delay", "5"), ("repeats", "3"), ("speed", "1"), ("gap", "1")):
            setattr(app, key, types.SimpleNamespace(get=lambda value=value: value))
        app.can_replace = Mock(return_value=True)
        app.refresh = Mock()
        app.status = Mock()
        app.macro = sample()
        app.macro["start_position"] = {"x": 10, "y": 10, "screens": [
            {"x": 0, "y": 0, "width": 100, "height": 100}]}
        with patch("nobamacro.sys.platform", "linux"), \
             patch("nobamacro.os.geteuid", return_value=1000, create=True), \
             patch("nobamacro.shutil.which", return_value="pkexec"), \
             patch("nobamacro.subprocess.Popen") as popen, \
             patch("nobamacro.threading.Thread"), \
             patch("nobamacro.messagebox.showerror") as error:
            for action in ("record", "play", "record"):
                app.start(action)
                self.assertIs(app.runtime, runtime)
                runtime.close.assert_not_called()
                app.process = None  # The previous operation has finished.
            error.assert_not_called()
            self.assertEqual(popen.call_count, 3)
            for call in popen.call_args_list:
                self.assertEqual(call.args[0][-1], str(Path("session-runtime") / "backend.py"))
        app.cleanup_runtime()
        runtime.close.assert_called_once()
        self.assertIsNone(app.runtime)


if __name__ == "__main__":
    unittest.main()
