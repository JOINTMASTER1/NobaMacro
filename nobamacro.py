#!/usr/bin/env python3
"""NobaMacro desktop UI. The UI always runs as the normal desktop user."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from macro_core import MAX_BYTES, validate_macro, validate_options
import desktop_pointer_client
from runtime_files import RuntimeFiles
from app_settings import DEFAULT_BINDINGS, FKEY_CODES, load_settings, save_settings, validate_bindings
from shortcut_client import ShortcutClient


class App:
    def __init__(self, root):
        self.root = root
        self.macro = None
        self.dirty = False
        self.process = None
        self.messages = queue.Queue()
        self.closing = False
        self.failed = False
        self.stop_requested = False
        self.pending_stop = None
        self.pipe_lock = threading.Lock()
        self.action = None
        self.minimized_for_playback = False
        self.runtime = None
        self.runtime_error = None
        self.busy = threading.Event()
        self.shortcuts_client = None
        self.shortcut_generation = 0
        self.last_hotkey = 0.0
        self.shortcut_cooldown_until = 0.0
        self.local_bindings = []
        settings_warning = ""
        try:
            self.settings = load_settings()
        except Exception as exc:
            self.settings = {"bindings": dict(DEFAULT_BINDINGS), "global_shortcuts": True}
            settings_warning = "Could not load preferences; defaults are active. " + str(exc)
        try:
            self.runtime = RuntimeFiles(Path(__file__).resolve().parent)
        except Exception as exc:
            self.runtime_error = str(exc)
        root.title("NobaMacro 2.4 — Mouse & Keyboard Recorder")
        root.geometry("820x660")
        root.minsize(760, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Sans", 24, "bold"))
        style.configure("Sub.TLabel", font=("Sans", 11))
        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        self.credit = ttk.Label(outer, text="Developed by Joint")
        self.credit.pack(side="bottom", anchor="e", pady=(8, 0))
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        frame = ttk.Frame(self.notebook, padding=16)
        self.settings_frame = ttk.Frame(self.notebook, padding=24)
        self.notebook.add(frame, text="Recorder")
        self.notebook.add(self.settings_frame, text="Settings")
        ttk.Label(frame, text="NobaMacro 2.4", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Mouse + keyboard recording for Linux", style="Sub.TLabel").pack(anchor="w", pady=(2, 20))
        self.info = tk.StringVar(value="No recording loaded")
        ttk.Label(frame, textvariable=self.info, wraplength=660).pack(anchor="w", pady=(0, 18))
        options = ttk.LabelFrame(frame, text="Playback & timing", padding=14)
        options.pack(fill="x")
        self.repeats = tk.StringVar(value="1")
        self.speed = tk.StringVar(value="1.0")
        self.delay = tk.StringVar(value="5")
        self.gap = tk.StringVar(value="1")
        self.auto_position = tk.BooleanVar(value=True)
        self.minimize_playback = tk.BooleanVar(value=False)
        self.fields = []
        for column, (label, variable, low, high, increment) in enumerate([
            ("Repetitions", self.repeats, 1, 100000, 1),
            ("Speed ×", self.speed, 0.1, 10, 0.1),
            ("Start delay (s)", self.delay, 0, 60, 1),
            ("Repeat pause (s)", self.gap, 0, 3600, 0.5),
        ]):
            options.columnconfigure(column, weight=1)
            ttk.Label(options, text=label).grid(row=0, column=column, sticky="w", padx=5)
            entry = ttk.Spinbox(options, textvariable=variable, from_=low, to=high,
                                increment=increment, width=12)
            entry.grid(row=1, column=column, sticky="ew", padx=5, pady=(6, 0))
            self.fields.append(entry)
        self.position_toggle = ttk.Checkbutton(frame,
            text="Save pointer start and restore it before every repetition (KDE Plasma)",
            variable=self.auto_position)
        self.position_toggle.pack(anchor="w", pady=(12, 0))
        self.minimize_toggle = ttk.Checkbutton(frame, text="Minimize window during playback",
                                               variable=self.minimize_playback)
        self.minimize_toggle.pack(anchor="w", pady=(5, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=20)
        self.record_button = ttk.Button(buttons, text="● Record", command=lambda: self.start("record"))
        self.play_button = ttk.Button(buttons, text="▶ Play", command=lambda: self.start("play"))
        self.stop_button = ttk.Button(buttons, text="■ Stop / F9")
        # Stop on press, not release. The helper confirms a real physical click.
        self.stop_button.bind("<ButtonPress-1>", self.stop_button_pressed)
        self.open_button = ttk.Button(buttons, text="Open…", command=self.open)
        self.save_button = ttk.Button(buttons, text="Save as…", command=self.save)
        for button in (self.record_button, self.play_button, self.stop_button, self.open_button, self.save_button):
            button.pack(side="left", padx=(0, 8))
        self.status = tk.StringVar(value="Ready. Record or open a saved macro.")
        if self.runtime_error:
            self.status.set(self.runtime_error)
        ttk.Label(frame, textvariable=self.status, wraplength=655,
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 18))
        ttk.Separator(frame).pack(fill="x")
        ttk.Label(frame, wraplength=655, justify="left", text=(
            "F9 stops recording or playback globally (use Fn+F9 if needed).\n"
            "With automatic positioning enabled, your pointer returns to the saved start before every loop. "
            "Focus the target window during the countdown; keep your mouse still during alignment.\n"
            "The terminating Stop click is removed. Replayed input cannot press the playback Stop control.\n"
            "Regular mice and keyboards only. Administrator authentication is requested per operation. "
            "Input is captured only while recording; saved macros contain your typed input."
        )).pack(anchor="w", pady=(14, 0))
        self.build_settings(settings_warning)
        self.refresh()
        root.after(80, self.poll)
        root.after(0, self.setup_shortcuts)

    def build_settings(self, warning):
        frame = self.settings_frame
        ttk.Label(frame, text="Keyboard shortcuts", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Choose function keys F1–F12. F9 always remains an emergency stop.",
                  wraplength=650).pack(anchor="w", pady=(8, 20))
        grid = ttk.Frame(frame)
        grid.pack(anchor="w", fill="x")
        self.shortcut_vars = {}
        self.shortcut_fields = []
        for row, (action, label) in enumerate((("record", "Record / Stop recording"), ("stop", "Stop"), ("play", "Play"))):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 24), pady=8)
            value = tk.StringVar(value=self.settings["bindings"][action])
            self.shortcut_vars[action] = value
            choices = [name for name in FKEY_CODES if action == "stop" or name != "F9"]
            field = ttk.Combobox(grid, textvariable=value, values=choices, state="readonly", width=16)
            field.grid(row=row, column=1, sticky="w", pady=8)
            self.shortcut_fields.append(field)
        self.global_shortcuts = tk.BooleanVar(value=self.settings["global_shortcuts"])
        self.global_toggle = ttk.Checkbutton(frame, variable=self.global_shortcuts,
                                             text="Enable global shortcuts while NobaMacro is open (KDE Plasma)")
        self.global_toggle.pack(anchor="w", pady=(20, 12))
        self.apply_settings_button = ttk.Button(frame, text="Save settings", command=self.apply_settings)
        self.apply_settings_button.pack(anchor="w")
        self.shortcut_status = tk.StringVar(value=warning or "Shortcuts are being initialized…")
        ttk.Label(frame, textvariable=self.shortcut_status, wraplength=650).pack(anchor="w", pady=16)
        ttk.Label(frame, wraplength=650, justify="left", text=(
            "Press Record again to finish recording. Play starts the loaded macro when idle. "
            "Stop ends recording or playback; F9 always works during input operations.\n\n"
            "The selected control keys are excluded from recordings and playback. "
            "Choose keys your target app does not need. If KDE already uses a key, choose another. "
            "With global shortcuts disabled, Record and Play shortcuts work only while this window has focus.\n\n"
            "Preferences are saved for the next launch. Shortcuts can be changed only while idle."
        )).pack(anchor="w")

    def setup_shortcuts(self):
        self.shortcut_generation += 1
        if self.shortcuts_client is not None:
            self.shortcuts_client.close()
            self.shortcuts_client = None
        for binding in self.local_bindings:
            self.root.unbind(binding)
        self.local_bindings.clear()
        for action, key in self.settings["bindings"].items():
            binding = f"<{key}>"
            self.root.bind(binding, lambda event, action=action: self.handle_shortcut(action))
            self.local_bindings.append(binding)
        self.stop_button.configure(text=f"■ Stop / {self.settings['bindings']['stop']}")
        if not self.settings["global_shortcuts"]:
            self.shortcut_status.set("Saved. Record/Play shortcuts are active in this window; physical Stop keys remain global during operations.")
            return
        if sys.platform != "linux" or self.runtime is None:
            self.shortcut_status.set("Window shortcuts active. Global shortcuts require KDE Plasma on Linux.")
            return
        try:
            self.shortcuts_client = ShortcutClient(self.runtime.path("kde_shortcuts.py"),
                self.settings["bindings"], self.messages, self.busy, self.shortcut_generation)
            self.shortcut_status.set("Connecting KDE global shortcuts…")
        except Exception as exc:
            self.shortcut_status.set("Window shortcuts active. Global shortcut error: " + str(exc))

    def apply_settings(self):
        if self.process is not None:
            return
        try:
            settings = {"bindings": validate_bindings({action: var.get() for action, var in self.shortcut_vars.items()}),
                        "global_shortcuts": self.global_shortcuts.get()}
            save_settings(settings)
            self.settings = settings
            self.setup_shortcuts()
        except Exception as exc:
            messagebox.showerror("Cannot save settings", str(exc), parent=self.root)

    def handle_shortcut(self, action, was_busy=False):
        now = time.monotonic()
        # Active operations accept physical controls in evdev. Never trust a
        # replayed KDE/Tk shortcut to stop or enqueue another operation.
        if was_busy or self.process is not None or now < self.shortcut_cooldown_until:
            return "break"
        if now - self.last_hotkey < 0.4:
            return "break"
        self.last_hotkey = now
        if action in ("record", "play"):
            self.start(action)
        return "break"

    def refresh(self):
        busy = self.process is not None
        for button in (self.record_button, self.open_button):
            button.configure(state="disabled" if busy else "normal")
        for button in (self.play_button, self.save_button):
            button.configure(state="normal" if not busy and self.macro and self.macro["events"] else "disabled")
        self.stop_button.configure(state="normal" if busy else "disabled")
        for entry in self.fields:
            entry.configure(state="disabled" if busy else "normal")
        self.position_toggle.configure(state="disabled" if busy else "normal")
        self.minimize_toggle.configure(state="disabled" if busy else "normal")
        for field in self.shortcut_fields:
            field.configure(state="disabled" if busy else "readonly")
        for widget in (self.global_toggle, self.apply_settings_button):
            widget.configure(state="disabled" if busy else "normal")

    def can_replace(self):
        return not self.dirty or messagebox.askyesno(
            "Unsaved recording", "Discard the unsaved recording?", parent=self.root)

    def start(self, action):
        if self.process:
            return
        try:
            if sys.platform != "linux":
                raise RuntimeError("Recording and playback require Linux. Run this application on Nobara.")
            if os.geteuid() == 0:
                raise RuntimeError("Start the desktop app without sudo. It elevates only the input helper.")
            if self.runtime is None:
                raise RuntimeError(self.runtime_error or "Application runtime unavailable; restart NobaMacro.")
            # Resolve both files before authentication or the unsaved-recording prompt.
            helper = self.runtime.path("backend.py")
            if self.auto_position.get():
                self.runtime.path("kde_pointer.py")
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise RuntimeError("pkexec is missing. Install polkit (see README).")
            options = validate_options({"action": action, "delay": float(self.delay.get()),
                                        "repeats": int(self.repeats.get()), "speed": float(self.speed.get()),
                                        "gap": float(self.gap.get()), "auto_position": self.auto_position.get(),
                                        "shortcuts": dict(self.settings["bindings"])})
            if action == "record" and not self.can_replace():
                return
            if action == "play":
                if not self.macro or not self.macro["events"]:
                    raise ValueError("Record or open a macro first")
                options["macro"] = validate_macro(self.macro)
                if options["auto_position"] and self.macro.get("start_position") is None:
                    raise ValueError("This recording has no saved start position. Record it again with automatic positioning enabled, or turn the option off for manual positioning.")
            # Use Nobara's system interpreter so dnf-installed evdev is available.
            process = subprocess.Popen([pkexec, "/usr/bin/python3", "-B", "-X", "faulthandler", "-u", str(helper)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8")
            self.process = process
            self.failed = False
            self.stop_requested = False
            self.pending_stop = None
            self.action = action
            self.busy.set()
            self.status.set("Waiting for administrator authentication…")
            self.refresh()
            threading.Thread(target=self.worker, args=(process, options), daemon=True).start()
        except Exception as exc:
            traceback.print_exc()
            messagebox.showerror("Cannot start", str(exc), parent=self.root)

    def worker(self, process, options):
        errors = []
        pointer = None

        def drain_errors():
            for line in process.stderr:
                print("Input helper: " + line.rstrip(), file=sys.stderr, flush=True)
                if sum(map(len, errors)) < 8000:
                    errors.append(line)

        reader = threading.Thread(target=drain_errors, daemon=True)
        reader.start()
        try:
            if options["auto_position"]:
                pointer = desktop_pointer_client.DesktopPointerClient(helper=self.runtime.path("kde_pointer.py"))
                pointer.get()  # Fail before starting input capture if the connection is unavailable.
            # The initial request is the only writer until it is completely flushed.
            self.send_command(process, options)
            process.request_sent = True
            self.messages.put({"type": "request_sent", "process": process})
            for line in process.stdout:
                item = json.loads(line)
                if item.get("type") == "pointer_request":
                    reply = {"type": "pointer_reply", "id": item["id"]}
                    try:
                        if pointer is None:
                            raise RuntimeError("Desktop pointer connection was not enabled")
                        reply["position"] = pointer.get()
                    except Exception as exc:
                        reply["error"] = str(exc)
                    try:
                        self.send_command(process, reply)
                    except (BrokenPipeError, ValueError):
                        # F9 may finish the helper while a desktop probe is in flight.
                        pass
                else:
                    self.messages.put(item)
        except Exception as exc:
            traceback.print_exc()
            self.messages.put({"type": "error", "text": str(exc)})
        finally:
            with self.pipe_lock:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if pointer is not None:
                try:
                    pointer.close()
                except Exception:
                    pass
            process.wait()
            reader.join(timeout=1)
            for pipe in (process.stdin, process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
            self.messages.put({"type": "exit", "code": process.returncode,
                               "detail": "".join(errors).strip()})

    def send_command(self, process, command):
        with self.pipe_lock:
            process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
            process.stdin.flush()

    def stop_button_pressed(self, _event):
        self.stop("button")
        return "break"

    def stop(self, source="close"):
        if self.process:
            command = {"type": "stop_button", "at": time.monotonic()} if source == "button" else {"type": "stop"}
            if not getattr(self.process, "request_sent", False):
                command = {"type": "stop"}  # Cancel before any operation has started.
                source = "close"
            if source != "button":
                self.stop_requested = True
            self.pending_stop = command
            self.status.set("Stop requested. If authentication is open, cancel that dialog.")
            if getattr(self.process, "request_sent", False):
                try:
                    self.send_command(self.process, command)
                    self.pending_stop = None
                except (OSError, ValueError):
                    pass

    def poll(self):
        try:
            while True:
                item = self.messages.get_nowait()
                kind = item["type"]
                if kind in ("shortcut", "shortcuts_ready", "shortcuts_error"):
                    if item.get("generation") != self.shortcut_generation:
                        continue
                    if kind == "shortcut":
                        self.handle_shortcut(item["action"], item.get("while_busy", False))
                    elif kind == "shortcuts_ready":
                        self.shortcut_status.set("Global shortcuts connected. Settings saved for future launches.")
                    else:
                        self.shortcut_status.set("Window shortcuts active. " + item["text"])
                elif kind == "request_sent":
                    item["process"].request_sent = True
                    if self.pending_stop is not None:
                        try:
                            self.send_command(item["process"], self.pending_stop)
                        except (OSError, ValueError):
                            pass
                        self.pending_stop = None
                    if self.action == "play" and not self.stop_requested and self.minimize_playback.get():
                        self.root.iconify()
                        self.minimized_for_playback = True
                elif kind == "stop_ack":
                    self.stop_requested = True
                    self.status.set("Stopping…")
                elif kind == "status" and not self.stop_requested:
                    self.status.set(item["text"])
                elif kind == "recording":
                    self.macro = validate_macro(item["macro"])
                    self.dirty = True
                    self.update_info("Unsaved recording")
                elif kind == "error":
                    self.failed = True
                    print("Operation failed: " + item["text"], file=sys.stderr, flush=True)
                    self.status.set("Error: " + item["text"])
                    if not self.closing:
                        self.root.deiconify()
                        self.minimized_for_playback = False
                        messagebox.showerror("NobaMacro", item["text"], parent=self.root)
                elif kind == "exit":
                    self.process = None
                    self.action = None
                    self.busy.clear()
                    self.shortcut_cooldown_until = time.monotonic() + 1.0
                    if item["code"] and not self.failed:
                        detail = item["detail"] or f"Input helper exited with code {item['code']}. Authentication may have been cancelled."
                        print(detail, file=sys.stderr, flush=True)
                        self.status.set(detail)
                        if not self.closing:
                            self.root.deiconify()
                            messagebox.showerror("Playback / recording stopped", detail, parent=self.root)
                    elif not self.failed:
                        self.status.set("Stopped. Recording ready to save or play." if self.dirty else "Finished / stopped. Ready.")
                    self.refresh()
                    if self.minimized_for_playback and not self.closing:
                        self.root.deiconify()
                    self.minimized_for_playback = False
                    if self.closing:
                        self.cleanup_runtime()
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        except Exception as exc:
            traceback.print_exc()
            self.failed = True
            self.stop()
            self.root.deiconify()
            self.status.set("Interface error: " + str(exc))
            if not self.closing:
                messagebox.showerror("NobaMacro interface error", str(exc), parent=self.root)
        self.root.after(80, self.poll)

    def update_info(self, name):
        position = self.macro.get("start_position")
        start_text = f" · Start ({position['x']}, {position['y']})" if position else " · No saved pointer start"
        self.info.set(f"{name} · {self.macro['duration']:.2f} seconds · "
                      f"{len(self.macro['events']):,} events" + start_text)

    def open(self):
        if not self.can_replace():
            return
        path = filedialog.askopenfilename(filetypes=[("NobaMacro recordings", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            with open(path, "rb") as source:
                payload = source.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("Macro file exceeds 100 MB")
            macro = validate_macro(json.loads(payload))
            self.macro = macro
            self.dirty = False
            self.update_info(Path(path).name)
            self.status.set("Loaded. Set repetitions, then Play.")
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Cannot open recording", str(exc), parent=self.root)

    def save(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="recording.json",
                                          filetypes=[("NobaMacro recording", "*.json")])
        if not path:
            return
        temp = None
        try:
            import tempfile
            # Private permissions, followed by an atomic replace to avoid partial saves.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(path).parent,
                                             prefix=".nobamacro-", delete=False) as target:
                temp = target.name
                json.dump(self.macro, target, separators=(",", ":"))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp, path)
            self.dirty = False
            self.update_info(Path(path).name)
            self.status.set("Recording saved.")
        except Exception as exc:
            if temp and os.path.exists(temp):
                os.unlink(temp)
            messagebox.showerror("Cannot save recording", str(exc), parent=self.root)

    def close(self):
        if self.process:
            self.closing = True
            self.stop()
        elif self.can_replace():
            self.cleanup_runtime()
            self.root.destroy()

    def cleanup_runtime(self):
        # Called only after helpers have exited, never between Record/Play operations.
        if getattr(self, "shortcuts_client", None) is not None:
            self.shortcuts_client.close()
            self.shortcuts_client = None
        if self.runtime is not None:
            try:
                self.runtime.close()
            except OSError:
                traceback.print_exc()
            self.runtime = None


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
