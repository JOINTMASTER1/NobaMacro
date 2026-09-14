"""Shortcut persistence, physical recording controls and idle dispatch regressions."""
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import backend
from app_settings import DEFAULT_BINDINGS, control_codes, load_settings, save_settings, validate_bindings
from nobamacro import App
from test_nobamacro import sample


class SettingsTests(unittest.TestCase):
    def test_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            self.assertEqual(load_settings(path)['bindings'], DEFAULT_BINDINGS)
            settings = {'bindings': {'record': 'F2', 'stop': 'F3', 'play': 'F12'}, 'global_shortcuts': False}
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_collisions_and_emergency_key_rejected(self):
        for bindings in ({'record': 'F2', 'stop': 'F2', 'play': 'F3'},
                         {'record': 'F9', 'stop': 'F2', 'play': 'F3'},
                         {'record': 'F2', 'stop': 'F3', 'play': 'F9'},
                         {'record': 'A', 'stop': 'F3', 'play': 'F4'}):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                validate_bindings(bindings)

    def test_failed_save_preserves_existing_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            settings = {'bindings': DEFAULT_BINDINGS, 'global_shortcuts': True}
            save_settings(settings, path)
            with patch('app_settings.os.replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    save_settings({**settings, 'global_shortcuts': False}, path)
            self.assertEqual(load_settings(path), settings)

    def test_custom_controls_excluded_and_all_stop_keys_end_recording(self):
        bindings = {'record': 'F2', 'stop': 'F3', 'play': 'F12'}
        for stop_code in (60, 61, 67):
            device = Mock()
            device.name = 'Keyboard'
            device.capabilities.return_value = {1: [30, 60, 61, 67, 88]}
            device.active_keys.return_value = []
            def event(code, value):
                return types.SimpleNamespace(type=1, code=code, value=value)
            batch = [(0.1, 0, event(30, 1)), (0.2, 0, event(30, 0)),
                     (0.3, 0, event(88, 1)), (0.4, 0, event(88, 0)),
                     (0.5, 0, event(stop_code, 1))]
            with patch.object(backend.time, 'monotonic', return_value=0), \
                 patch.object(backend, 'read_events', return_value=batch), patch.object(backend, 'emit') as emit:
                backend.record([device], {'delay': 0, 'shortcuts': bindings}, threading.Event())
            macro = next(call.kwargs['macro'] for call in emit.call_args_list if call.args[0] == 'recording')
            self.assertEqual([e[3] for e in macro['events'] if e[2] == 1], [30, 30])
            self.assertEqual(macro['duration'], 0.5)
        self.assertEqual(control_codes(bindings), {60, 61, 67, 88})

    def test_busy_and_delayed_shortcuts_cannot_restart_playback(self):
        app = App.__new__(App)
        app.start = Mock()
        app.last_hotkey = 0
        app.shortcut_cooldown_until = 0
        with patch('nobamacro.time.monotonic', return_value=10):
            app.process = object()
            app.handle_shortcut('record')
            app.process = None
            app.handle_shortcut('play', was_busy=True)
            app.shortcut_cooldown_until = 11
            app.handle_shortcut('play')
            app.start.assert_not_called()
            app.shortcut_cooldown_until = 0
            app.handle_shortcut('play')
            app.handle_shortcut('play')  # Duplicate KDE/Tk notification.
            app.start.assert_called_once_with('play')

    def test_newly_assigned_keys_in_old_macro_are_not_replayed(self):
        macro = sample()
        macro['devices'][0]['capabilities']['1'].append(88)
        macro['events'].extend([[0.6, 0, 1, 88, 1], [0.7, 0, 1, 88, 0]])
        options = {'macro': macro, 'shortcuts': {'record': 'F2', 'stop': 'F3', 'play': 'F12'},
                   'delay': 0, 'repeats': 3, 'speed': 1, 'gap': 0}
        with patch.object(backend.threading, 'Thread'), patch.object(backend, 'wait_until', return_value=True), \
             patch.object(backend, 'emit'), patch.object(backend, 'play_events') as play:
            backend.playback(Mock(), [], options, threading.Event())
        played = play.call_args.args[0]
        self.assertFalse(any(e[2] == 1 and e[3] == 88 for e in played['events']))
        self.assertEqual(play.call_args.args[3], 3)
        self.assertEqual(len(macro['events']), 6)  # The saved original is unchanged.
