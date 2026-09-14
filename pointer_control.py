"""Verified pointer placement using compositor coordinates and relative uinput.

No guessed screen coordinates, XWayland cursor queries, or accumulated raw deltas.
"""
from collections import deque
import math
import time

from macro_core import contains, screen_layout, validate_position, wait_until


def crossing(a, b):
    """Return points on both sides of a shared monitor boundary."""
    ax, ay, aw, ah = (a[k] for k in ("x", "y", "width", "height"))
    bx, by, bw, bh = (b[k] for k in ("x", "y", "width", "height"))
    left, right = max(ax, bx), min(ax + aw, bx + bw)
    top, bottom = max(ay, by), min(ay + ah, by + bh)
    if top < bottom:
        y = (top + bottom - 1) // 2
        if ax + aw == bx:
            return [(bx - 1, y), (bx, y)]
        if bx + bw == ax:
            return [(ax, y), (ax - 1, y)]
    if left < right:
        x = (left + right - 1) // 2
        if ay + ah == by:
            return [(x, by - 1), (x, by)]
        if by + bh == ay:
            return [(x, ay), (x, ay - 1)]
    if left < right and top < bottom:
        return [((left + right - 1) // 2, (top + bottom - 1) // 2)]
    return None


def route(current, target):
    screens = current["screens"]
    starts = [i for i, s in enumerate(screens) if contains(s, current["x"], current["y"])]
    ends = {i for i, s in enumerate(screens) if contains(s, target["x"], target["y"])}
    pending = deque((i, []) for i in starts)
    visited = set(starts)
    while pending:
        index, points = pending.popleft()
        if index in ends:
            return points + [(target["x"], target["y"])]
        for neighbor, screen in enumerate(screens):
            bridge = crossing(screens[index], screen)
            if neighbor not in visited and bridge is not None:
                visited.add(neighbor)
                pending.append((neighbor, points + bridge))
    raise RuntimeError("The saved pointer position is on a disconnected monitor area. Arrange monitors with touching edges in Display Settings.")


def restore_pointer(target, output, probe, stop, clock=time.monotonic):
    validate_position(target)
    current = validate_position(probe())
    layout = screen_layout(target)
    if screen_layout(current) != layout:
        raise RuntimeError("Monitor layout or scaling changed since recording. Restore the original display settings or record again.")
    # Route around empty desktop areas in L-shaped monitor arrangements.
    waypoints = route(current, target)
    gain = [1.0, 1.0]
    deadline = clock() + 20
    for tx, ty in waypoints:
        previous = None
        sent = None
        for _ in range(120):
            if stop.is_set():
                return
            if clock() >= deadline:
                raise RuntimeError("Could not restore the pointer within 20 seconds. Check pointer confinement and mouse settings.")
            if previous is not None:
                current = validate_position(probe())
                if screen_layout(current) != layout:
                    raise RuntimeError("Monitor layout changed during pointer alignment.")
                for axis, key in enumerate(("x", "y")):
                    moved = current[key] - previous[key]
                    if sent[axis] and moved / sent[axis] > 0:
                        gain[axis] = max(0.05, min(50, abs(moved / sent[axis])))
            error = [tx - current["x"], ty - current["y"]]
            tolerance = 1
            if max(abs(e) for e in error) <= tolerance:
                break
            sent = []
            for axis, delta in enumerate(error):
                if abs(delta) <= tolerance:
                    sent.append(0)
                else:
                    # Adapt to pointer acceleration/scaling using measured movement.
                    raw = delta / gain[axis] * (0.75 if abs(delta) > 4 else 1)
                    limit = 64 if previous is None else 600
                    sent.append(int(math.copysign(max(1, min(limit, round(abs(raw)))), raw)))
            previous = current
            for axis, value in enumerate(sent):
                if value:
                    output.write(2, axis, value)
            output.syn()
            if not wait_until(clock() + 0.04, stop, clock):
                return
        else:
            raise RuntimeError("Pointer could not reach its saved position. Playback stopped before sending macro input.")
