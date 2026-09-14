"""Portable macro validation and interruptible timing; no Linux dependency."""
import math
import time
from app_settings import validate_bindings

MAX_EVENTS = 1_000_000
MAX_SECONDS = 86_400
MAX_BYTES = 100_000_000
STOP_KEY = 67  # Linux KEY_F9


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return value


def integer(value, low, high, label):
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return number(value, low, high, label)


def validate_macro(macro):
    if not isinstance(macro, dict) or macro.get("format") != "NobaMacro" or macro.get("version") not in (1, 2):
        raise ValueError("Not a supported NobaMacro recording")
    if macro.get("start_position") is not None:
        validate_position(macro["start_position"])
    duration = number(macro.get("duration"), 0, MAX_SECONDS, "Duration")
    devices = macro.get("devices")
    if not isinstance(devices, list) or not 1 <= len(devices) <= 64:
        raise ValueError("Recording must contain 1–64 devices")
    caps = []
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get("name"), str):
            raise ValueError("Invalid device")
        if len(device["name"]) > 256:
            raise ValueError("Device name is too long")
        source = device.get("capabilities")
        if not isinstance(source, dict) or not source or set(source) - {"1", "2"}:
            raise ValueError("Only keyboard and relative mouse input is supported")
        converted = {}
        for kind, codes in source.items():
            if not isinstance(codes, list) or not codes or len(codes) > 768:
                raise ValueError("Invalid device capabilities")
            converted[int(kind)] = {
                integer(code, 0, 767 if kind == "1" else 15, "Input code") for code in codes
            }
        caps.append(converted)
    events = macro.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("Recording is too large or has invalid events")
    previous = 0.0
    for event in events:
        if not isinstance(event, list) or len(event) != 5:
            raise ValueError("Invalid event")
        at, device, kind, code, value = event
        number(at, previous, duration, "Event time")
        previous = at
        integer(device, 0, len(devices) - 1, "Device index")
        integer(kind, 0, 2, "Event type")
        integer(code, 0, 767, "Event code")
        integer(value, -(2**31), 2**31 - 1, "Event value")
        if kind == 0:
            if code != 0 or value != 0:
                raise ValueError("Only SYN_REPORT synchronization is allowed")
        elif code not in caps[device].get(kind, set()):
            raise ValueError("Event is not supported by its device")
        if kind == 1 and (value not in (0, 1, 2) or code == STOP_KEY):
            raise ValueError("Invalid key value or reserved F9 key")
    return macro


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError("Invalid options")
    number(options.get("delay"), 0, 60, "Start delay")
    integer(options.get("repeats", 1), 1, 100_000, "Repeat count")
    number(options.get("speed", 1), 0.1, 10, "Speed")
    number(options.get("gap", 0), 0, 3600, "Repeat pause")
    if type(options.get("auto_position", False)) is not bool:
        raise ValueError("Automatic positioning must be true or false")
    if "shortcuts" in options:
        validate_bindings(options["shortcuts"])
    return options


def validate_position(position):
    if not isinstance(position, dict):
        raise ValueError("Invalid desktop pointer position")
    for key in ("x", "y"):
        integer(position.get(key), -1_000_000, 1_000_000, "Pointer " + key)
    screens = position.get("screens")
    if not isinstance(screens, list) or not 1 <= len(screens) <= 64:
        raise ValueError("Invalid monitor layout")
    for screen in screens:
        if not isinstance(screen, dict):
            raise ValueError("Invalid monitor geometry")
        for key in ("x", "y"):
            integer(screen.get(key), -1_000_000, 1_000_000, "Monitor " + key)
        for key in ("width", "height"):
            integer(screen.get(key), 1, 1_000_000, "Monitor " + key)
    if not any(contains(screen, position["x"], position["y"]) for screen in screens):
        raise ValueError("Pointer position is outside the connected monitors")
    return position


def contains(screen, x, y):
    return (screen["x"] <= x < screen["x"] + screen["width"] and
            screen["y"] <= y < screen["y"] + screen["height"])


def screen_layout(position):
    return sorted(tuple(s[key] for key in ("x", "y", "width", "height")) for s in position["screens"])


def finish_recording(macro, button_stop_at=None):
    """Remove the *actual* terminating click, never an arbitrary last N seconds."""
    events = sorted(macro["events"], key=lambda event: event[0])
    events = [event for event in events if not (event[2] == 1 and event[3] == STOP_KEY)]
    if button_stop_at is not None:
        # UI stops on ButtonPress, not release, so a long press cannot get saved.
        candidates = [event[0] for event in events
                      if event[2:5] == [1, 272, 1] and 0 <= button_stop_at - event[0] <= 0.75]
        if not candidates:
            raise RuntimeError("Could not identify the Stop click reliably. Please record again and stop with F9.")
        cutoff = max(candidates)
        events = [event for event in events if event[0] < cutoff]
        macro["duration"] = min(macro["duration"], cutoff)
    # Flush any frame cut short by F9/Stop, without inventing button presses.
    dirty = set()
    for _, device, kind, _, _ in events:
        if kind == 0:
            dirty.discard(device)
        else:
            dirty.add(device)
    for device in sorted(dirty):
        events.append([macro["duration"], device, 0, 0, 0])
    macro["events"] = events
    return validate_macro(macro)


def wait_until(deadline, stop, clock=time.monotonic):
    """Use absolute deadlines so event processing doesn't accumulate timing drift."""
    while not stop.is_set():
        remaining = deadline - clock()
        if remaining <= 0:
            return True
        stop.wait(min(remaining, 0.05))
    return False


def play_events(macro, outputs, stop, repeats=1, speed=1, gap=0,
                progress=lambda current, total: None, clock=time.monotonic,
                before_repeat=lambda: None):
    held = [set() for _ in outputs]

    def release():
        # Attempt every release even if another virtual device has failed.
        for output, keys in zip(outputs, held):
            for code in list(keys):
                try:
                    output.write(1, code, 0)
                except OSError:
                    pass
            keys.clear()
            try:
                output.syn()
            except OSError:
                pass

    try:
        for loop in range(repeats):
            if stop.is_set():
                return
            before_repeat()
            if stop.is_set():
                return
            progress(loop + 1, repeats)
            start = clock()
            for at, device, kind, code, value in macro["events"]:
                if not wait_until(start + at / speed, stop, clock):
                    return
                output = outputs[device]
                if kind == 0:
                    output.syn()
                else:
                    # Track before writing so cleanup also covers partial failures.
                    if kind == 1:
                        if value in (1, 2):
                            held[device].add(code)
                    output.write(kind, code, value)
                    if kind == 1 and value == 0:
                        held[device].discard(code)
            if not wait_until(start + macro["duration"] / speed, stop, clock):
                return
            release()
            if loop + 1 < repeats and not wait_until(clock() + gap, stop, clock):
                return
    finally:
        release()
