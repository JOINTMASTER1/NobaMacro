import json
import struct
import sys
import types
import unittest
from unittest.mock import patch

import backend
from macro_core import play_events, validate_macro, validate_options


def sample():
    return {"format": "NobaMacro", "version": 1, "duration": 1.0,
            "devices": [{"name": "Keyboard", "capabilities": {"1": [30, 48]}}],
            "events": [[0.1, 0, 1, 30, 1], [0.1, 0, 0, 0, 0],
                       [0.4, 0, 1, 30, 0], [0.4, 0, 0, 0, 0]]}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeStop:
    def __init__(self, clock, cancel_at=None):
        self.clock = clock
        self.cancel_at = cancel_at
        self.stopped = False

    def set(self):
        self.stopped = True

    def is_set(self):
        return self.stopped or (self.cancel_at is not None and self.clock() >= self.cancel_at)

    def wait(self, seconds):
        self.clock.now += seconds
        return self.is_set()


class Output:
    def __init__(self, clock, write_cost=0):
        self.clock = clock
        self.events = []
        self.write_cost = write_cost

    def write(self, kind, code, value):
        self.events.append((self.clock(), kind, code, value))
        self.clock.now += self.write_cost

    def syn(self):
        self.events.append((self.clock(), 0, 0, 0))


class ValidationTests(unittest.TestCase):
    def test_json_roundtrip(self):
        macro = sample()
        self.assertEqual(validate_macro(json.loads(json.dumps(macro))), macro)

    def test_bad_event_and_time_inputs(self):
        for event in ([float("nan"), 0, 1, 30, 1], [float("inf"), 0, 1, 30, 1],
                      [-1, 0, 1, 30, 1], [2, 0, 1, 30, 1], [0.1, 4, 1, 30, 1],
                      [0.1, 0, 1, 99, 1], [0.1, 0, 1, 30, 3],
                      [0.1, 0, 0, 3, 0], [0.1, 0, 3, 0, 0],
                      [0.1, False, 1, 30, 1], [0.1, 0, 1, 30, True]):
            with self.subTest(event=event):
                macro = sample()
                macro["events"] = [event]
                with self.assertRaises(ValueError):
                    validate_macro(macro)

    def test_unsorted_events(self):
        macro = sample()
        macro["events"].reverse()
        with self.assertRaises(ValueError):
            validate_macro(macro)

    def test_reserved_stop_key_rejected(self):
        macro = sample()
        macro["devices"][0]["capabilities"]["1"].append(67)
        macro["events"] = [[0.1, 0, 1, 67, 1]]
        with self.assertRaises(ValueError):
            validate_macro(macro)

    def test_unsupported_capability_rejected(self):
        macro = sample()
        macro["devices"][0]["capabilities"]["3"] = [0]
        with self.assertRaises(ValueError):
            validate_macro(macro)

    def test_options_boundaries(self):
        options = {"delay": 5, "repeats": 2, "speed": 1, "gap": 0}
        self.assertEqual(validate_options(options), options)
        for key, value in [("speed", 0), ("speed", float("nan")), ("delay", -1),
                           ("repeats", 0), ("repeats", 2.5), ("gap", 3601)]:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    validate_options({**options, key: value})


class PlaybackTests(unittest.TestCase):
    def setup_play(self, cancel_at=None, write_cost=0):
        clock = FakeClock()
        stop = FakeStop(clock, cancel_at)
        output = Output(clock, write_cost)
        return clock, stop, output

    def test_repeat_speed_and_trailing_pause(self):
        clock, stop, output = self.setup_play()
        progress = []
        play_events(sample(), [output], stop, repeats=2, speed=2, gap=0.3,
                    clock=clock, progress=lambda i, n: progress.append((i, n)))
        downs = [event[0] for event in output.events if event[1:] == (1, 30, 1)]
        self.assertAlmostEqual(downs[0], 0.05)
        self.assertAlmostEqual(downs[1], 0.85)
        self.assertAlmostEqual(clock(), 1.3)
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_processing_does_not_accumulate_drift(self):
        clock, stop, output = self.setup_play(write_cost=0.02)
        play_events(sample(), [output], stop, clock=clock)
        up = next(event for event in output.events if event[1:] == (1, 30, 0))
        self.assertAlmostEqual(up[0], 0.4)

    def test_cancel_releases_key_and_mouse(self):
        clock, stop, keyboard = self.setup_play(cancel_at=0.25)
        mouse = Output(clock)
        macro = sample()
        macro["devices"].append({"name": "Mouse", "capabilities": {"1": [272], "2": [0, 1]}})
        macro["events"] = [[0.1, 0, 1, 30, 1], [0.1, 1, 1, 272, 1],
                           [0.8, 0, 1, 30, 0], [0.8, 1, 1, 272, 0]]
        validate_macro(macro)
        play_events(macro, [keyboard, mouse], stop, repeats=10, clock=clock)
        self.assertLess(clock(), 0.4)
        self.assertEqual([e[1:] for e in keyboard.events if e[1] == 1], [(1, 30, 1), (1, 30, 0)])
        self.assertEqual([e[1:] for e in mouse.events if e[1] == 1], [(1, 272, 1), (1, 272, 0)])

    def test_incomplete_recording_releases_each_loop(self):
        clock, stop, output = self.setup_play()
        macro = sample()
        macro["events"] = [[0.1, 0, 1, 30, 1]]
        play_events(macro, [output], stop, repeats=3, clock=clock)
        self.assertEqual([e[3] for e in output.events if e[1] == 1], [1, 0, 1, 0, 1, 0])

    def test_exception_releases_other_devices(self):
        clock, stop, first = self.setup_play()
        second = Output(clock)
        macro = sample()
        macro["events"] = [[0, 0, 1, 30, 1], [0.1, 1, 1, 48, 1]]
        second.write = lambda *_: (_ for _ in ()).throw(OSError("disconnected"))
        with self.assertRaises(OSError):
            play_events(macro, [first, second], stop, clock=clock)
        self.assertIn((1, 30, 0), [e[1:] for e in first.events])

    def test_cancel_before_first_event(self):
        clock, stop, output = self.setup_play(cancel_at=0)
        play_events(sample(), [output], stop, clock=clock)
        self.assertFalse(any(e[1] == 1 for e in output.events))

    def test_failed_keyup_is_retried_during_cleanup(self):
        clock, stop, output = self.setup_play()
        original = output.write
        failed = []

        def write(kind, code, value):
            if value == 0 and not failed:
                failed.append(True)
                raise OSError("temporary failure")
            original(kind, code, value)

        output.write = write
        with self.assertRaises(OSError):
            play_events(sample(), [output], stop, clock=clock)
        self.assertIn((1, 30, 0), [event[1:] for event in output.events])

    def test_cancel_during_repeat_pause(self):
        clock, stop, output = self.setup_play(cancel_at=1.2)
        play_events(sample(), [output], stop, repeats=5, gap=5, clock=clock)
        self.assertEqual(sum(e[1:] == (1, 30, 1) for e in output.events), 1)
        self.assertLess(clock(), 1.3)


class RecordingTests(unittest.TestCase):
    def test_monotonic_clock_ioctl(self):
        ioctl = unittest.mock.Mock()
        device = types.SimpleNamespace(fd=12)
        with patch.dict(sys.modules, {"fcntl": types.SimpleNamespace(ioctl=ioctl)}):
            backend.set_monotonic_clock(device)
        ioctl.assert_called_once_with(12, 0x400445A0, struct.pack("i", 1))

    def test_records_timestamps_and_excludes_f9(self):
        clock = FakeClock()
        stop = FakeStop(clock)

        class Device:
            name = "Test keyboard"
            def capabilities(self):
                return {0: [0], 1: [30, 67]}
            def active_keys(self):
                return []

        class Event:
            def __init__(self, kind, code, value):
                self.type, self.code, self.value = kind, code, value

        batches = iter([
            [(0.1, 0, Event(1, 30, 1)), (0.1, 0, Event(0, 0, 0))],
            [(0.3, 0, Event(1, 30, 0)), (0.3, 0, Event(0, 0, 0))],
            [(0.5, 0, Event(1, 67, 1))]
        ])
        messages = []
        with patch.object(backend.time, "monotonic", clock), \
             patch.object(backend, "read_events", side_effect=lambda *_: next(batches)), \
             patch.object(backend, "emit", side_effect=lambda kind, **kw: messages.append((kind, kw))):
            backend.record([Device()], {"delay": 0}, stop)
        macro = next(fields["macro"] for kind, fields in messages if kind == "recording")
        self.assertEqual(macro["duration"], 0.5)
        self.assertEqual([e[0] for e in macro["events"]], [0.1, 0.1, 0.3, 0.3])
        self.assertFalse(any(e[2] == 1 and e[3] == 67 for e in macro["events"]))
        validate_macro(macro)

    def test_overflow_fails_instead_of_saving_incomplete_input(self):
        clock = FakeClock()
        stop = FakeStop(clock)
        device = unittest.mock.Mock()
        device.name = "Keyboard"
        device.capabilities.return_value = {1: [30, 67]}
        device.active_keys.return_value = []
        event = unittest.mock.Mock(type=0, code=3)
        with patch.object(backend.time, "monotonic", clock), \
             patch.object(backend, "read_events", return_value=[(0.1, 0, event)]), \
             patch.object(backend, "emit") as emit:
            with self.assertRaisesRegex(RuntimeError, "dropped"):
                backend.record([device], {"delay": 0}, stop)
        self.assertFalse(any(call.args[0] == "recording" for call in emit.call_args_list))


if __name__ == "__main__":
    unittest.main()
