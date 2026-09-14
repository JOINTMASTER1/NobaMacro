"""Check that a failed desktop subprocess becomes an error, not a UI exit."""
import io
import queue
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import patch

from desktop_pointer_client import DesktopPointerClient
from nobamacro import App
from test_nobamacro import sample


class DesktopProcessTests(unittest.TestCase):
    def client_running(self, script):
        real_popen = subprocess.Popen
        def launch(_command, **kwargs):
            return real_popen([sys.executable, "-u", "-c", script], **kwargs)
        with patch("desktop_pointer_client.subprocess.Popen", side_effect=launch):
            return DesktopPointerClient()

    def test_child_answers_multiple_probes_and_closes(self):
        client = self.client_running('''
import sys, json
for line in sys.stdin:
    if json.loads(line)["type"] == "quit":
        break
    print(json.dumps({"position": {"x": 10, "y": 20, "screens": [
        {"x": 0, "y": 0, "width": 100, "height": 100}]}}), flush=True)
''')
        try:
            self.assertEqual(client.get()["x"], 10)
            self.assertEqual(client.get()["y"], 20)
        finally:
            client.close()
        self.assertEqual(client.process.returncode, 0)
        client.close()  # Repeated cleanup is harmless.

    def test_abrupt_child_exit_does_not_exit_parent(self):
        client = self.client_running('''
import os, sys
sys.stdin.readline()
print("simulated helper failure", file=sys.stderr, flush=True)
os._exit(23)
''')
        try:
            with self.assertRaisesRegex(RuntimeError, "exited.*23"):
                client.get()
        finally:
            client.close()
        self.assertEqual(client.process.returncode, 23)

    def test_child_python_error_returns_actionable_message(self):
        client = self.client_running('''
import sys, json
for line in sys.stdin:
    if json.loads(line)["type"] == "quit":
        break
    print(json.dumps({"error": "KWin connection failed"}), flush=True)
''')
        try:
            with self.assertRaisesRegex(RuntimeError, "KWin connection failed"):
                client.get()
        finally:
            client.close()

    def test_worker_keeps_recording_when_desktop_helper_fails(self):
        app = App.__new__(App)
        app.messages = queue.Queue()
        app.pipe_lock = threading.Lock()
        app.runtime = types.SimpleNamespace(path=lambda _: "test-helper.py")
        saved = sample()
        app.macro = saved
        process = types.SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(),
                                        stderr=io.StringIO(), wait=lambda: 0, returncode=0)
        with patch("desktop_pointer_client.DesktopPointerClient", side_effect=RuntimeError("desktop child crashed")), \
             patch("nobamacro.traceback.print_exc"):
            app.worker(process, {"auto_position": True})
        self.assertIs(app.macro, saved)
        messages = []
        while not app.messages.empty():
            messages.append(app.messages.get_nowait())
        self.assertEqual([message["type"] for message in messages], ["error", "exit"])
        self.assertIn("desktop child crashed", messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
