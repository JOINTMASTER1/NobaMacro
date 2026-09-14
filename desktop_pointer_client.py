"""Keep native D-Bus/GLib libraries out of the Tk desktop process."""
from collections import deque
import contextlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

from macro_core import validate_position


class DesktopPointerClient:
    def __init__(self, helper=None):
        helper = Path(helper) if helper is not None else Path(__file__).resolve().with_name("kde_pointer.py")
        if not helper.is_file():
            raise RuntimeError(f"KDE helper file is missing:\n{helper}\nClose the app and extract the complete NobaMacro ZIP again.")
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-X", "faulthandler", "-u", str(helper), "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")
        self.replies = queue.Queue()
        self.errors = deque(maxlen=40)
        self.closed = False
        self.output_thread = threading.Thread(target=self.read_output, daemon=True)
        self.error_thread = threading.Thread(target=self.read_errors, daemon=True)
        self.output_thread.start()
        self.error_thread.start()

    def read_output(self):
        try:
            for line in self.process.stdout:
                try:
                    self.replies.put(json.loads(line))
                except ValueError:
                    self.replies.put({"error": "Invalid response from the KDE pointer helper"})
        finally:
            self.replies.put(None)

    def read_errors(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip()[:1000])
            print("KDE helper: " + line.rstrip(), file=sys.stderr, flush=True)

    def failure(self):
        try:
            code = self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            code = None
        self.error_thread.join(timeout=0.2)
        if code is not None and code < 0:
            reason = f"crashed (signal {-code})"
        else:
            reason = f"exited (code {code})" if code is not None else "closed its connection"
        detail = "\n".join(self.errors)[-2500:]
        return RuntimeError(f"KDE pointer helper {reason}. The recording is still in the app."
                            + ("\n" + detail if detail else ""))

    def get(self):
        if self.closed:
            raise RuntimeError("KDE pointer connection is closed")
        try:
            self.process.stdin.write('{"type":"get"}\n')
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise self.failure() from None
        try:
            reply = self.replies.get(timeout=15)
        except queue.Empty:
            raise RuntimeError("KDE pointer helper did not respond within 15 seconds") from None
        if reply is None:
            raise self.failure()
        if not isinstance(reply, dict):
            raise RuntimeError("Invalid KDE pointer response")
        if reply.get("error"):
            raise RuntimeError(str(reply["error"]))
        return validate_position(reply.get("position"))

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.write('{"type":"quit"}\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()  # Only this app's unprivileged pointer child.
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.output_thread.join(timeout=1)
        self.error_thread.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()
