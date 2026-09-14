"""Native shortcut registration is isolated from the GUI and sends actions only."""
import contextlib
import json
import subprocess
import sys
import threading


class ShortcutClient:
    def __init__(self, helper, bindings, messages, busy, generation):
        self.closed = False
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-X", "faulthandler", "-u", str(helper), json.dumps(bindings)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")

        def read_output():
            try:
                for line in self.process.stdout:
                    item = json.loads(line)
                    item["generation"] = generation
                    item["while_busy"] = busy.is_set()
                    messages.put(item)
            except Exception as exc:
                messages.put({"type": "shortcuts_error", "text": str(exc), "generation": generation})
            finally:
                if not self.closed:
                    messages.put({"type": "shortcuts_error", "text": "Global shortcut helper exited. Reapply Settings to retry.", "generation": generation})

        def read_errors():
            for line in self.process.stderr:
                print("Shortcut helper: " + line.rstrip(), file=sys.stderr, flush=True)

        self.output_thread = threading.Thread(target=read_output, daemon=True)
        self.error_thread = threading.Thread(target=read_errors, daemon=True)
        self.output_thread.start()
        self.error_thread.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.terminate()
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
