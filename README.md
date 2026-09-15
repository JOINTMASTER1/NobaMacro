# NobaMacro 2.4 — Nobara Official / KDE Plasma

Mouse and keyboard recording with timing, repeated playback, adjustable speed, a physical F9 emergency stop, and automatic pointer positioning.

## Support development

NobaMacro is free and open source. If it saves you time, you can optionally [support Joint on Ko-fi](https://ko-fi.com/jointmaster). Thank you for supporting its development!

## Update and launch

**Version 2.4 adds a Settings tab .** Choose separate F1–F12 keys for Record/Stop recording, Stop, and Play. Defaults are F8, F9 and F10. F9 always remains an emergency stop. Click **Save settings** to apply. Duplicate keys are rejected.

Global shortcuts use KDE Plasma while NobaMacro is open. Turn them off to keep idle Record/Play shortcuts local to this window; physical stop controls still work globally during operations. The Record key finishes an active recording; the Stop key ends recording or playback. Selected control keys are excluded from capture and playback, including when opening older macros. Pick keys your target application does not need. KDE shortcut conflicts may require selecting another key. Preferences live in `~/.config/NobaMacro/settings.json` (or under `XDG_CONFIG_HOME`).

Install the dependencies, including the KDE integration packages:

```bash
sudo dnf install python3 python3-tkinter python3-evdev polkit python3-dbus python3-gobject
```

From inside the extracted `NobaMacro` folder:

```bash
bash launch.sh
```

Run as your normal desktop user, not with sudo. The app requests administrator authentication for each recording/playback operation. The KDE connection must use your desktop user session.

## What changed

- **Stop-button clicks are removed from new recordings.** Stop is handled on mouse-button press. The corresponding physical press and following input are cut from the recording; earlier legitimate clicks and pauses remain. Movement toward Stop remains, but the terminating click does not.
- **F9 is excluded from saved recordings.** Playback monitors the physical keyboard, excluding NobaMacro's virtual devices.
- **A replayed Stop click cannot activate the playback Stop control on its own.** The helper requires a matching physical mouse press. Minimizing during playback is optional and off by default.
- **Automatic pointer start is enabled by default.** KDE's real global cursor coordinates are saved when recording begins. Before the first playback and every subsequent repetition, the pointer returns to that location and its position is checked before macro input starts.
- **Loops no longer need to finish at their starting point.** Every repetition aligns independently, after the configured repeat pause.

## Record and play

1. Leave **Save pointer start and restore it before every repetition** checked.
2. Set a **Start delay**, for example 5 seconds, and click **Record**. Authenticate.
3. During the countdown, focus the target app and position the pointer where the macro should begin. Release all keys/buttons and keep the pointer still until the status reads **RECORDING**. This location is saved.
4. Perform your mouse/keyboard actions and pauses.
5. Stop with **F9** (possibly **Fn+F9**), or physically click the app's **Stop** button.
6. Use **Save as…** to save the JSON macro. Its summary shows `Start (x, y)`.
7. Set **Repetitions**, **Speed ×**, **Start delay**, and **Repeat pause** as desired.
8. Click **Play** and authenticate. The app remains visible unless **Minimize window during playback** is checked. Focus the target window during the countdown. **Manual pointer alignment is no longer needed.** Keep your mouse still during automatic alignment.
9. Each repetition restores the pointer, plays the actions, releases virtual held keys/buttons, and waits for the repeat pause.

Physical **F9** stops playback during alignment, the countdown, playback or the repeat pause. You can also physically click Stop in the NobaMacro window; restore it from the taskbar first if you chose to minimize it. Keyboard activation of the Stop button is not used; F9 is the keyboard stop control.

## Pointer positioning details

Automatic positioning targets **KDE Plasma 6**, including Nobara Official. It reads `workspace.cursorPos` using a short-lived, read-only KWin script in your desktop session. The script is unloaded after the query; no KWin plugin is permanently installed or enabled.

The helper moves the pointer through uinput and queries KDE again to measure the result. This feedback accounts for pointer speed/scaling during alignment. Alignment must reach within **one logical pixel** of the saved position before recorded input begins. Alignment time is separate from macro timing and the repeat pause.

Multi-monitor layouts with touching edges, including L-shaped arrangements and negative coordinates, are routed through shared edges. Changed display layouts, inaccessible targets or pointer confinement produce an error instead of playback from an unverified location. Restore the original layout or re-record. Keep the physical mouse still during alignment. Alignment has a 20-second budget, with an additional bounded query timeout if KDE stops responding.

Movement **within** each repetition still uses relative events. Keep acceleration, pointer speed, scaling, window positions and keyboard layout consistent. KDE may apply separate settings to the `NobaMacro` virtual mouse; match those to the physical mouse. Start-position restoration does not restore window contents, select the original target window or undo the previous loop's changes. The target app must be ready for the next repetition.

Turning automatic positioning off keeps manual relative playback available, including legacy files and other desktops. In that mode, align manually and design loops to return to their start. Re-recording is recommended for all version 1 macros.

## Supported input and limits

Regular relative USB/Bluetooth mice and keyboards exposed through evdev are supported. All matching connected devices are recorded together. Connect them before starting. Touchpads, touchscreens, tablets, controllers and gestures are not supported.

Repeat counts: 1–100,000. Speed: 0.1×–10×. Recordings are limited to approximately one million events or 24 hours. High-rate mice can reach the event limit quickly. Input overflow and device disconnection stop the operation with an error. A physical keyboard with F9 is required.

Timing uses monotonic kernel timestamps and absolute playback deadlines. Desktop load can still affect timing, and held-key repetition depends on desktop/app settings. Physical F9 is not exclusively grabbed and may also reach the focused application.

## Troubleshooting

- **Playback crash:** launch with `bash launch.sh`. If the application disappears or an error is shown, share the error and `~/.local/state/NobaMacro/last-run.log` before launching again (each launch replaces that log). If `XDG_STATE_HOME` is set, use the path printed by the launcher. The log captures errors and stack traces, not the macro's event data. Keep the log only as long as needed for diagnosis.
- **Missing KDE packages:** run the install command above. Automatic positioning requires `python3-dbus` and `python3-gobject` in system Python.
- **KWin connection error:** run the new app as your normal user inside KDE Plasma. The read-only diagnostic below prints the actual cursor position and monitor geometry; include its output when reporting a problem:

```bash
/usr/bin/python3 kde_pointer.py
```

- **Missing `/dev/uinput`:** run `sudo modprobe uinput` and retry.
- **Authentication:** use your normal KDE session with its polkit agent active. When stopping/closing during authentication, cancel that dialog so the pending helper can exit.
- **Stop button unresponsive:** use physical F9. Button stops require a matching physical mouse press; unusual input forwarding or very long desktop-input delays can prevent a match.
- **Old file has no saved start:** record it again with automatic positioning enabled. The old file never stored screen coordinates.
- **Missing application file:** close all old NobaMacro windows, extract the entire ZIP into a permanent folder, and run that folder's `launch.sh`. Do not copy only the launcher or individual Python files. Version 2.3 keeps its own helper copy while open, but the complete installation is still needed at startup. Keep saved macro JSON files separate from program files.

## Local data and permissions

The GUI and KDE connection run as the desktop user. Only the short-lived evdev/uinput helper is privileged; it exits after each operation and stops if the parent pipe closes. No device permissions or group memberships are changed. No background service is installed and no network connection is made.

Recordings contain typed input, including sensitive text typed while recording. JSON saves use owner-only permissions. Only play recordings you trust. Launching the app or opening a file does not start recording/playback. Closing while active stops and exits, so save important recordings before closing.

## Validation

```bash
python3 -m unittest -v
```

## Source and references

- `nobamacro.py`: desktop UI and helper communication.
- `backend.py`: privileged capture, physical stop monitoring and playback.
- `macro_core.py`: validation, Stop-click trimming and scheduling.
- `kde_pointer.py`: desktop-user KWin cursor queries.
- `desktop_pointer_client.py`: isolated KDE process lifecycle and error reporting.
- `runtime_files.py`: complete helper snapshot retained for the open app session. Helper interpreters disable bytecode writes so the privileged helper cannot leave root-owned cache files there.
- `pointer_control.py`: verified positioning and monitor routing.
- `test_nobamacro.py`, `test_regressions.py`, `test_kde_pointer.py`: portable tests, including the overloaded KDE method regression.
- `test_desktop_process.py`: real subprocess tests for abrupt helper failure and retained recordings.
- `test_runtime_files.py`: installation-file loss and Record → Play → Record lifecycle tests.

References: [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/), [KWin scripting tutorial](https://develop.kde.org/docs/plasma/kwin/), [python-evdev tutorial](https://python-evdev.readthedocs.io/en/latest/tutorial.html).

## Publishing and license

Developed by Joint. Distributed under the MIT license; see `LICENSE`.

See [PUBLISHING.md](PUBLISHING.md) for uploading source to GitHub, making a release and the remaining Flatpak work. This source release is not a Flatpak package.

Additional source files: `app_settings.py` validates and saves preferences; `kde_shortcuts.py` registers KDE shortcuts in an isolated process; `shortcut_client.py` manages that process. `test_settings.py` covers persistence, invalid shortcuts, custom physical controls and busy-operation dispatch.
