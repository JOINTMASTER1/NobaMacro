"""Regression coverage for Stop clicks and restoring the pointer every repetition."""
import json
import threading
import types
import unittest
from unittest.mock import Mock, patch

import backend
from macro_core import finish_recording, play_events, validate_macro, validate_position
from pointer_control import restore_pointer, route
from test_nobamacro import FakeClock, FakeStop, Output, sample


def position(x=100, y=100, screens=None):
    return {"x": x, "y": y, "screens": screens or [{"x": 0, "y": 0, "width": 1920, "height": 1080}]}


def mouse_macro():
    macro = sample()
    macro["devices"] = [{"name": "Mouse", "capabilities": {"1": [272], "2": [0, 1]}}]
    macro["events"] = [[0.1, 0, 1, 272, 1], [0.1, 0, 0, 0, 0],
                       [0.2, 0, 1, 272, 0], [0.2, 0, 0, 0, 0],
                       [0.8, 0, 2, 0, 15], [0.8, 0, 0, 0, 0],
                       [0.9, 0, 1, 272, 1], [0.91, 0, 1, 272, 0],
                       [0.91, 0, 0, 0, 0]]
    return macro


class StopRegressionTests(unittest.TestCase):
    def test_stop_press_removed_and_three_repetitions_finish(self):
        macro = finish_recording(mouse_macro(), button_stop_at=0.92)
        self.assertEqual(macro["duration"], 0.9)
        self.assertEqual([event[0] for event in macro["events"] if event[2] == 1], [0.1, 0.2])
        clock = FakeClock()
        stop = FakeStop(clock)
        output = Output(clock)
        play_events(macro, [output], stop, repeats=3, clock=clock)
        self.assertEqual(sum(event[1:] == (1, 272, 1) for event in output.events), 3)
        self.assertAlmostEqual(clock(), 2.7)

    def test_only_terminating_click_removed_not_earlier_work(self):
        macro = mouse_macro()
        macro["events"].insert(6, [0.85, 0, 2, 1, 8])
        finish_recording(macro, button_stop_at=0.91)
        self.assertIn([0.85, 0, 2, 1, 8], macro["events"])
        self.assertIn([0.2, 0, 1, 272, 0], macro["events"])

    def test_f9_removed_even_if_present_in_raw_buffer(self):
        macro = sample()
        macro["events"].extend([[0.8, 0, 1, 67, 1], [0.9, 0, 1, 67, 0]])
        finish_recording(macro)
        self.assertEqual(len(macro["events"]), 4)

    def test_unmatched_stop_click_fails_without_guessing(self):
        with self.assertRaisesRegex(RuntimeError, "identify"):
            finish_recording(mouse_macro(), button_stop_at=5)

    def test_replayed_ui_click_cannot_stop_helper(self):
        stop = threading.Event()
        controls = backend.Controls(stop)
        with patch.object(backend, "emit"), patch.object(backend.time, "monotonic", return_value=5):
            controls.handle({"type": "stop_button", "at": 5})
            controls.expire_click()
        self.assertFalse(stop.is_set())
        self.assertIsNone(controls.button_cutoff)

    def test_play_button_physical_click_is_too_old_to_confirm_replay(self):
        stop = threading.Event()
        controls = backend.Controls(stop)
        controls.physical_event(1, types.SimpleNamespace(type=1, code=272, value=1))
        with patch.object(backend, "emit"), patch.object(backend.time, "monotonic", return_value=8):
            controls.handle({"type": "stop_button", "at": 7})
            controls.expire_click()
        self.assertFalse(stop.is_set())

    def test_real_stop_click_works_in_both_arrival_orders(self):
        for command_first in (True, False):
            with self.subTest(command_first=command_first):
                stop = threading.Event()
                controls = backend.Controls(stop)
                event = types.SimpleNamespace(type=1, code=272, value=1)
                command = {"type": "stop_button", "at": 5.01}
                with patch.object(backend, "emit"), patch.object(backend.time, "monotonic", return_value=5.02):
                    if command_first:
                        controls.handle(command)
                        controls.physical_event(5, event)
                    else:
                        controls.physical_event(5, event)
                        controls.handle(command)
                    controls.expire_click()
                self.assertTrue(stop.is_set())
                self.assertEqual(controls.button_cutoff, 5)

    def test_command_waits_for_queued_physical_stop_press(self):
        stop = threading.Event()
        controls = backend.Controls(stop)
        event = types.SimpleNamespace(type=1, code=272, value=1)
        controls.physical_event(4.9, event)  # Legitimate target click shortly before Stop.
        with patch.object(backend, "emit"), patch.object(backend.time, "monotonic", return_value=5.02):
            controls.handle({"type": "stop_button", "at": 5.01})
            self.assertFalse(stop.is_set())
            controls.physical_event(5, event)
            controls.expire_click()
        self.assertEqual(controls.button_cutoff, 5)

    def test_virtual_devices_not_used_for_emergency_monitor(self):
        physical = Mock(name="physical")
        physical.name = "USB keyboard"
        physical.capabilities.return_value = {1: [30, 67]}
        virtual = Mock(name="virtual")
        virtual.name = "NobaMacro 0"
        virtual.capabilities.return_value = {1: [30, 67]}
        evdev = types.SimpleNamespace(list_devices=lambda: ["a", "b"],
                                     InputDevice=lambda path: physical if path == "a" else virtual)
        with patch.object(backend, "set_monotonic_clock"):
            self.assertEqual(backend.discover(evdev), [physical])
        virtual.close.assert_called_once()

    def test_backend_recording_matches_and_removes_stop_click(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        controls = backend.Controls(stop)
        device = Mock()
        device.name = "Mouse and keyboard"
        device.capabilities.return_value = {1: [30, 67, 272], 2: [0, 1]}
        device.active_keys.return_value = []
        event = lambda kind, code, value: types.SimpleNamespace(type=kind, code=code, value=value)
        batches = iter([
            [(0.1, 0, event(1, 272, 1)), (0.2, 0, event(1, 272, 0)), (0.2, 0, event(0, 0, 0))],
            [(0.9, 0, event(1, 272, 1)), (0.9, 0, event(0, 0, 0))],
        ])
        def read(*_):
            batch = next(batches)
            if batch[0][0] == 0.9:
                clock.now = 0.91
                controls.handle({"type": "stop_button", "at": 0.91})
            return batch
        with patch.object(backend, "read_events", side_effect=read), \
             patch.object(backend.time, "monotonic", clock), patch.object(backend, "emit") as emit:
            backend.record([device], {"delay": 0}, stop, controls)
        macro = next(call.kwargs["macro"] for call in emit.call_args_list if call.args[0] == "recording")
        self.assertEqual([event[0] for event in macro["events"] if event[2] == 1], [0.1, 0.2])
        self.assertEqual(macro["duration"], 0.9)


class PointerRegressionTests(unittest.TestCase):
    def test_saved_position_roundtrip_and_legacy_compatibility(self):
        macro = sample()
        validate_macro(macro)
        macro.update(version=2, start_position=position())
        self.assertEqual(validate_macro(json.loads(json.dumps(macro))), macro)

    def test_bad_positions_rejected(self):
        for bad in (position(x=float("nan")), position(x=4000), position(screens=[{"x": 0}])):
            with self.subTest(position=bad), self.assertRaises(ValueError):
                validate_position(bad)

    def simulate(self, gain=1, start=(1700, 900), target=(123, 234)):
        clock = FakeClock()
        stop = FakeStop(clock)
        current = list(start)
        output = Output(clock)
        original_write = output.write
        def write(kind, code, value):
            original_write(kind, code, value)
            current[code] += value * gain
            current[code] = max(0, min((1919, 1079)[code], current[code]))
        output.write = write
        probe = lambda: position(x=round(current[0]), y=round(current[1]))
        restore_pointer(position(*target), output, probe, stop, clock=clock)
        return current, output, clock

    def test_alignment_from_different_start_positions_and_speeds(self):
        for gain in (0.25, 0.7, 1, 1.8, 2.5):
            for start in ((1700, 900), (20, 25), (123, 234)):
                with self.subTest(gain=gain, start=start):
                    current, output, _ = self.simulate(gain=gain, start=start)
                    self.assertLessEqual(abs(round(current[0]) - 123), 1)
                    self.assertLessEqual(abs(round(current[1]) - 234), 1)
                    self.assertFalse(any(event[1] == 1 for event in output.events))

    def test_restore_runs_before_each_of_three_repeats(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        output = Output(clock)
        starts = []
        def align():
            starts.append(clock())
            clock.now += 0.4
        play_events(sample(), [output], stop, repeats=3, gap=0.2, clock=clock, before_repeat=align)
        self.assertEqual(len(starts), 3)
        downs = [event[0] for event in output.events if event[1:] == (1, 30, 1)]
        for start, down in zip(starts, downs):
            self.assertAlmostEqual(down - start, 0.5)  # Query time precedes recorded timing.

    def test_three_loops_restore_even_when_each_finishes_elsewhere(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        current = [1600, 800]
        output = Output(clock)
        starts = []
        original = output.write
        def write(kind, code, value):
            original(kind, code, value)
            if kind == 2 and code in (0, 1):
                current[code] += value
        output.write = write
        target = position(200, 300)
        probe = lambda: position(*current)
        macro = mouse_macro()
        macro["events"] = [[0, 0, 2, 0, 100], [0, 0, 0, 0, 0]]
        play_events(macro, [output], stop, repeats=3, clock=clock,
                    before_repeat=lambda: restore_pointer(target, output, probe, stop, clock=clock),
                    progress=lambda *_: starts.append(tuple(current)))
        self.assertEqual(len(starts), 3)
        for x, y in starts:
            self.assertLessEqual(abs(x - 200), 1)
            self.assertLessEqual(abs(y - 300), 1)
        self.assertGreaterEqual(current[0], 299)  # Each macro ends 100 px away from its start.

    def test_layout_change_stops_before_any_injection(self):
        clock = FakeClock()
        output = Output(clock)
        changed = position(screens=[{"x": 0, "y": 0, "width": 1280, "height": 720}])
        with self.assertRaisesRegex(RuntimeError, "layout"):
            restore_pointer(position(), output, lambda: changed, FakeStop(clock), clock=clock)
        self.assertEqual(output.events, [])

    def test_cancel_during_alignment_does_not_play_macro(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        output = Output(clock)
        play_events(sample(), [output], stop, repeats=3, before_repeat=stop.set, clock=clock)
        self.assertFalse(any(event[1] == 1 for event in output.events))

    def test_confined_pointer_fails_without_running_macro(self):
        clock = FakeClock()
        output = Output(clock)
        stop = FakeStop(clock)
        with self.assertRaisesRegex(RuntimeError, "could not reach"):
            restore_pointer(position(600, 500), output, lambda: position(), stop, clock=clock)

    def test_routes_around_missing_corner_between_monitors(self):
        screens = [{"x": 0, "y": 0, "width": 100, "height": 100},
                   {"x": 100, "y": 0, "width": 100, "height": 100},
                   {"x": 100, "y": 100, "width": 100, "height": 100}]
        points = route(position(20, 20, screens), position(150, 150, screens))
        self.assertEqual(points, [(99, 49), (100, 49), (149, 99), (149, 100), (150, 150)])

    def test_negative_monitor_coordinates(self):
        screens = [{"x": -1920, "y": 0, "width": 1920, "height": 1080},
                   {"x": 0, "y": 0, "width": 1920, "height": 1080}]
        target = position(-500, 500, screens)
        validate_position(target)
        self.assertEqual(route(position(500, 500, screens), target)[-1], (-500, 500))

    def test_disconnected_monitor_areas_fail_clearly(self):
        screens = [{"x": 0, "y": 0, "width": 100, "height": 100},
                   {"x": 300, "y": 0, "width": 100, "height": 100}]
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            route(position(20, 20, screens), position(350, 50, screens))

    def test_recording_stores_desktop_position_at_record_start(self):
        clock = FakeClock()
        clock.now = 10
        stop = FakeStop(clock)
        controls = Mock()
        controls.pointer.return_value = position(345, 456)
        controls.button_cutoff = None
        device = Mock()
        device.name = "Keyboard"
        device.capabilities.return_value = {1: [30, 67]}
        device.active_keys.return_value = []
        batch = [(10.1, 0, types.SimpleNamespace(type=1, code=30, value=1)),
                 (10.2, 0, types.SimpleNamespace(type=1, code=30, value=0)),
                 (10.5, 0, types.SimpleNamespace(type=1, code=67, value=1))]
        with patch.object(backend, "read_events", return_value=batch), \
             patch.object(backend.time, "monotonic", clock), patch.object(backend, "emit") as emit:
            backend.record([device], {"delay": 0, "auto_position": True}, stop, controls)
        macro = next(call.kwargs["macro"] for call in emit.call_args_list if call.args[0] == "recording")
        self.assertEqual(macro["version"], 2)
        self.assertEqual(macro["start_position"], position(345, 456))
        self.assertAlmostEqual(macro["events"][0][0], 0.1)

    def test_helper_pointer_response_is_correlated(self):
        controls = backend.Controls(threading.Event())
        def emit(kind, **fields):
            self.assertEqual(kind, "pointer_request")
            controls.handle({"type": "pointer_reply", "id": fields["id"] - 1, "position": position(1, 2)})
            self.assertIsNone(controls.reply)
            controls.handle({"type": "pointer_reply", "id": fields["id"], "position": position(34, 56)})
        with patch.object(backend, "emit", side_effect=emit):
            self.assertEqual(controls.pointer(), position(34, 56))

    def test_helper_pointer_error_propagates(self):
        controls = backend.Controls(threading.Event())
        def emit(kind, **fields):
            controls.handle({"type": "pointer_reply", "id": fields["id"], "error": "KWin unavailable"})
        with patch.object(backend, "emit", side_effect=emit), self.assertRaisesRegex(RuntimeError, "KWin unavailable"):
            controls.pointer()

    def test_helper_query_remains_cancellable(self):
        controls = backend.Controls(threading.Event())
        with patch.object(backend, "emit"), self.assertRaises(InterruptedError):
            controls.pointer(pump=controls.stop.set)


if __name__ == "__main__":
    unittest.main()
