#!/usr/bin/env python3
"""One-operation privileged helper. JSON over pipes; never writes recordings to disk."""
import contextlib
import json
import os
import select
import signal
import sys
import threading
import time

from macro_core import (MAX_BYTES, MAX_EVENTS, MAX_SECONDS, STOP_KEY,
                        finish_recording, number, play_events, validate_macro,
                        validate_options, validate_position, wait_until)
from pointer_control import restore_pointer
from app_settings import DEFAULT_BINDINGS, FKEY_CODES, control_codes

OUTPUT_LOCK = threading.Lock()


def emit(kind, **fields):
    with OUTPUT_LOCK:
        print(json.dumps({"type": kind, **fields}, separators=(",", ":")), flush=True)


class Controls:
    """Stop requests are matched against the physical input stream, not UI events."""
    def __init__(self, stop):
        self.stop = stop
        self.lock = threading.Lock()
        self.pending_click = None
        self.last_click = None
        self.button_cutoff = None
        self.next_id = 0
        self.waiting_id = None
        self.reply = None

    def handle(self, command):
        if not isinstance(command, dict):
            raise ValueError("Invalid helper command")
        with self.lock:
            kind = command.get("type")
            if kind == "stop_button":
                self.pending_click = number(command.get("at"), 0, 10**12, "Stop timestamp")
            elif kind == "stop":
                self.stop.set()
                emit("stop_ack", reason="close")
            elif kind == "pointer_reply" and command.get("id") == self.waiting_id:
                self.reply = command

    def match_click(self):
        if self.pending_click is not None and self.last_click is not None:
            if 0 <= self.pending_click - self.last_click <= 0.75:
                self.button_cutoff = self.last_click
                self.pending_click = None
                self.stop.set()
                emit("stop_ack", reason="physical_button")

    def physical_event(self, timestamp, event):
        with self.lock:
            if event.type == 1 and event.code == 272 and event.value == 1:
                self.last_click = timestamp

    def expire_click(self):
        with self.lock:
            # Match only AFTER the input reader has processed its queued batch.
            # Matching in the command thread could mistake a previous click for Stop.
            self.match_click()
            if self.pending_click is not None and time.monotonic() - self.pending_click > 0.8:
                self.pending_click = None
                emit("status", text="Ignored a replayed Stop click. Press physical F9 to stop.")

    def pointer(self, pump=None):
        with self.lock:
            self.next_id += 1
            self.waiting_id = self.next_id
            self.reply = None
            request_id = self.next_id
        emit("pointer_request", id=request_id)
        deadline = time.monotonic() + 12
        while not self.stop.is_set():
            if pump is not None:
                pump()
                if self.stop.is_set():
                    break
            with self.lock:
                reply = self.reply
            if reply is not None:
                if reply.get("error"):
                    raise RuntimeError("Pointer positioning: " + str(reply["error"]))
                return validate_position(reply.get("position"))
            if time.monotonic() >= deadline:
                raise RuntimeError("Desktop pointer query timed out")
            self.stop.wait(0.01)
        raise InterruptedError("Stopped during pointer positioning")


def set_monotonic_clock(device):
    import fcntl
    import struct
    # Linux input.h: EVIOCSCLOCKID = _IOW('E', 0xa0, int).
    # Nobara x86_64 uses the asm-generic ioctl encoding; CLOCK_MONOTONIC = 1.
    fcntl.ioctl(device.fd, 0x400445A0, struct.pack("i", 1))


def discover(evdev):
    devices = []
    try:
        for path in sorted(evdev.list_devices()):
            device = evdev.InputDevice(path)
            caps = device.capabilities()
            keys = caps.get(1, [])
            rel = caps.get(2, [])
            supported = 30 in keys or (272 in keys and 0 in rel and 1 in rel)
            if device.name.startswith("NobaMacro ") or not supported:
                device.close()
                continue
            devices.append(device)
            set_monotonic_clock(device)
        if not devices:
            raise RuntimeError("No supported keyboard or relative mouse found. Connect a regular mouse/keyboard.")
        if not any(STOP_KEY in d.capabilities().get(1, []) for d in devices):
            raise RuntimeError("No keyboard with F9 found; an emergency stop keyboard is required.")
        return devices
    except Exception:
        for device in devices:
            device.close()
        raise


def read_events(devices, timeout=0.05):
    ready, _, _ = select.select(devices, [], [], timeout)
    batch = []
    through = time.monotonic()
    for device in ready:
        try:
            # Drain queued frames, including a Stop press behind high-rate motion.
            for _ in range(256):
                events = list(device.read())
                batch.extend((event.timestamp(), devices.index(device), event) for event in events)
                if not events or events[-1].timestamp() >= through:
                    break
        except BlockingIOError:
            pass
    batch.sort(key=lambda row: row[0])
    return batch


def description(device):
    return {"name": device.name[:256], "capabilities": {
        str(kind): codes for kind, codes in device.capabilities().items() if kind in (1, 2) and codes
    }}


def record(devices, options, stop, controls=None):
    bindings = options.get("shortcuts", DEFAULT_BINDINGS)
    stop_keys = {STOP_KEY, FKEY_CODES[bindings["stop"]], FKEY_CODES[bindings["record"]]}
    reserved = control_codes(bindings)
    def before_record_events(timeout=0.0):
        for timestamp, _, event in read_events(devices, timeout):
            if event.type == 1 and event.code in stop_keys and event.value == 1:
                stop.set()
            if controls is not None:
                controls.physical_event(timestamp, event)
        if controls is not None:
            controls.expire_click()

    emit("status", text=f"Starting in {options['delay']:g} seconds. Release all keys; F9 stops.")
    start = time.monotonic() + options["delay"]
    # Read during countdown so stale input cannot become part of the recording.
    while not stop.is_set() and time.monotonic() < start:
        before_record_events(min(0.05, max(0, start - time.monotonic())))
    if stop.is_set():
        return
    position = None
    if options.get("auto_position"):
        emit("status", text="Saving the starting pointer position…")
        position = controls.pointer(pump=before_record_events)
        # Cursor queries occur before timing begins; query latency is not recorded.
        start = time.monotonic()
    if any(d.active_keys() for d in devices):
        raise RuntimeError("A key or mouse button was held at the start. Release it and record again.")
    macro = {"format": "NobaMacro", "version": 2, "duration": 0,
             "devices": [description(d) for d in devices], "events": []}
    if position is not None:
        macro["start_position"] = position
    dirty = set()
    emit("status", text=f"RECORDING — press {bindings['record']}, {bindings['stop']} or F9 to finish")
    last_report = time.monotonic()
    end = None
    while not stop.is_set():
        for timestamp, index, event in read_events(devices):
            if timestamp < start:
                continue
            at = max(0.0, timestamp - start)
            if at >= MAX_SECONDS - 1 or len(macro["events"]) >= MAX_EVENTS - 64:
                end = min(at, MAX_SECONDS)
                stop.set()
                emit("status", text="Recording limit reached; saving captured input.")
                break
            if event.type == 1 and event.code in reserved:
                if event.code in stop_keys and event.value == 1:
                    end = at
                    stop.set()
                    break
                continue
            if event.type == 0 and event.code == 3:
                raise RuntimeError("Input events were dropped by Linux. Recording discarded; try a shorter recording or lower mouse polling rate.")
            if event.type in (1, 2):
                macro["events"].append([at, index, event.type, event.code, event.value])
                dirty.add(index)
            elif event.type == 0 and event.code == 0 and index in dirty:
                macro["events"].append([at, index, 0, 0, 0])
                dirty.discard(index)
            if controls is not None:
                # Append first, so the matched stopping press can be removed precisely.
                controls.physical_event(timestamp, event)
                if controls.button_cutoff is not None:
                    end = controls.button_cutoff - start
                    break
        now = time.monotonic()
        if len(macro["events"]) >= MAX_EVENTS - 64 or now - start >= MAX_SECONDS - 1:
            end = now - start
            stop.set()
            emit("status", text="Recording limit reached; saving captured input.")
        if now - last_report >= 1:
            emit("status", text=f"RECORDING — {now - start:.0f}s · {len(macro['events']):,} events · F9 stops")
            last_report = now
        if controls is not None:
            controls.expire_click()
    # Separate device batches can arrive slightly out of timestamp order.
    macro["events"].sort(key=lambda event: event[0])
    macro["duration"] = max(end if end is not None else time.monotonic() - start,
                            macro["events"][-1][0] if macro["events"] else 0)
    cutoff = controls.button_cutoff - start if controls is not None and controls.button_cutoff is not None else None
    finish_recording(macro, button_stop_at=cutoff)
    emit("recording", macro=macro)


def playback(evdev, devices, options, stop, controls=None):
    macro = validate_macro(options.get("macro"))
    bindings = options.get("shortcuts", DEFAULT_BINDINGS)
    stop_keys = {STOP_KEY, FKEY_CODES[bindings["stop"]]}
    reserved = control_codes(bindings)
    # Old recordings may contain a newly assigned shortcut. Never replay it into KDE.
    macro = {**macro, "events": [event for event in macro["events"]
                                if not (event[2] == 1 and event[3] in reserved)]}
    if not macro["events"]:
        raise ValueError("Recording is empty")
    failure = []

    def emergency_monitor():
        try:
            while not stop.is_set():
                for timestamp, _, event in read_events(devices):
                    if controls is not None:
                        controls.physical_event(timestamp, event)
                    if event.type == 1 and event.code in stop_keys and event.value == 1:
                        stop.set()
                    if event.type == 0 and event.code == 3:
                        raise RuntimeError("Emergency keyboard input overflow; playback stopped.")
                if controls is not None:
                    controls.expire_click()
        except Exception as exc:
            failure.append(str(exc))
            stop.set()

    monitor = threading.Thread(target=emergency_monitor, daemon=True)
    monitor.start()
    try:
        with contextlib.ExitStack() as stack:
            outputs = []
            for index, device in enumerate(macro["devices"]):
                caps = {int(kind): codes for kind, codes in device["capabilities"].items()}
                output = evdev.UInput(caps, name=f"NobaMacro {index}", phys=f"nobamacro/input{index}")
                stack.callback(output.close)
                outputs.append(output)
            pointer_output = None
            if options.get("auto_position"):
                if macro.get("start_position") is None:
                    raise RuntimeError("This older recording has no starting pointer position. Record it again with automatic positioning enabled.")
                for index, device in enumerate(macro["devices"]):
                    rel = device["capabilities"].get("2", [])
                    if 0 in rel and 1 in rel:
                        pointer_output = outputs[index]
                        break
                if pointer_output is None:
                    pointer_output = evdev.UInput({1: [272, 273], 2: [0, 1]}, name="NobaMacro Positioner")
                    stack.callback(pointer_output.close)
            # Allow udev/libinput to discover the new devices before the visible countdown.
            emit("status", text="Preparing virtual input devices…")
            if not wait_until(time.monotonic() + 1.5, stop):
                return
            emit("status", text=f"Playback in {options['delay']:g}s — focus your target window. F9 stops.")
            if not wait_until(time.monotonic() + options["delay"], stop):
                return
            def align():
                if options.get("auto_position"):
                    emit("status", text="Returning to the saved pointer position… F9 stops.")
                    restore_pointer(macro["start_position"], pointer_output, controls.pointer, stop)

            play_events(macro, outputs, stop, options["repeats"], options["speed"], options["gap"],
                        progress=lambda i, total: emit("status", text=f"PLAYING — repeat {i}/{total} · F9 stops"),
                        before_repeat=align)
    finally:
        stop.set()
        monitor.join(timeout=1)
        if failure:
            raise RuntimeError(failure[0])


def main():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("This helper requires Linux and administrator access through pkexec.")
    import evdev
    line = sys.stdin.readline(MAX_BYTES + 1)
    if len(line) > MAX_BYTES:
        raise ValueError("Request is too large")
    options = validate_options(json.loads(line))
    if options.get("action") not in ("record", "play"):
        raise ValueError("Unknown action")
    stop = threading.Event()
    controls = Controls(stop)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())

    def watch_parent():
        try:
            while not stop.is_set():
                line = sys.stdin.readline(100_001)
                if not line:
                    break
                if len(line) > 100_000:
                    raise ValueError("Helper command is too large")
                controls.handle(json.loads(line))
        except Exception as exc:
            emit("error", text=str(exc))
        finally:
            # Parent crash/closed pipe always ends the operation.
            stop.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    devices = discover(evdev)
    try:
        emit("status", text="Connected: " + ", ".join(d.name for d in devices))
        if options["action"] == "record":
            record(devices, options, stop, controls)
        else:
            playback(evdev, devices, options, stop, controls)
    finally:
        for device in devices:
            device.close()
    emit("done")


if __name__ == "__main__":
    try:
        main()
    except InterruptedError:
        emit("done")
    except Exception as exc:
        with contextlib.suppress(BrokenPipeError):
            emit("error", text=str(exc))
        sys.exit(1)
