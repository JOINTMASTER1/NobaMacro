"""Exercise the cursor probe against a proxy with Qt's overloaded-method metadata."""
import json
import types
import unittest
from unittest.mock import Mock, patch

from kde_pointer import KDEPointer


class KDEPointerTests(unittest.TestCase):
    def make_pointer(self, directory):
        pointer = KDEPointer.__new__(KDEPointer)
        pointer.temp = types.SimpleNamespace(name=directory)
        pointer.plugin = "test-probe"
        pointer.context = Mock()
        pointer.bus = Mock()
        pointer.bus.get_unique_name.return_value = ":1.42"
        pointer.service = None

        class DBusError(Exception):
            def get_dbus_name(self):
                return "org.freedesktop.DBus.Error.UnknownObject"

        pointer.dbus = types.SimpleNamespace(Interface=lambda *_: runner,
                    exceptions=types.SimpleNamespace(DBusException=DBusError))
        manager = Mock()
        runner = Mock()
        position = {"x": 320, "y": 240,
                    "screens": [{"x": 0, "y": 0, "width": 1920, "height": 1080}]}

        def load_script(*args, **kwargs):
            # Qt exports the default-argument overload too. A proxy may retain
            # the one-string signature even when the caller supplies two strings.
            signature = kwargs.get("signature", "s")
            if len(signature) < len(args):
                raise TypeError("Fewer items found in D-Bus signature than in Python arguments")
            self.assertEqual(signature, "ss")
            self.assertEqual(len(args), 2)
            return 7

        def run(**kwargs):
            self.assertEqual(kwargs.get("signature"), "")
            pointer.result = json.dumps(position)

        manager.loadScript.side_effect = load_script
        runner.run.side_effect = run
        pointer.manager = manager
        return pointer, manager, runner, position, DBusError

    def test_overloaded_load_script_uses_two_string_signature(self):
        with patch("kde_pointer.Path.write_text"):
            pointer, manager, _, expected, _ = self.make_pointer("test-probe-directory")
            self.assertEqual(pointer.get(), expected)
            manager.unloadScript.assert_called_once_with("test-probe", signature="s", timeout=3)
            pointer.bus.get_object.assert_called_with("org.kde.KWin", "/Scripting/Script7", introspect=False)

    def test_failed_run_still_unloads_and_identifies_stage(self):
        with patch("kde_pointer.Path.write_text"):
            pointer, manager, runner, _, _ = self.make_pointer("test-probe-directory")
            runner.run.side_effect = RuntimeError("permission denied")
            with self.assertRaisesRegex(RuntimeError, "running.*permission denied"):
                pointer.get()
            manager.unloadScript.assert_called_once_with("test-probe", signature="s", timeout=3)

    def test_older_script_path_fallback(self):
        with patch("kde_pointer.Path.write_text"):
            pointer, manager, runner, expected, error = self.make_pointer("test-probe-directory")
            run_success = runner.run.side_effect
            calls = []
            def run(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    raise error("old object path")
                return run_success(**kwargs)
            runner.run.side_effect = run
            self.assertEqual(pointer.get(), expected)
            self.assertEqual(runner.run.call_count, 2)
            pointer.bus.get_object.assert_called_with("org.kde.KWin", "/7", introspect=False)


if __name__ == "__main__":
    unittest.main()
