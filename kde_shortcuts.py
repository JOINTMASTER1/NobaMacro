"""Register only the configured shortcuts, in a separate KDE desktop process."""
import contextlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import traceback
import uuid

from app_settings import validate_bindings


def emit(kind, **fields):
    print(json.dumps({"type": kind, **fields}), flush=True)


def main():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    bindings = validate_bindings(json.loads(sys.argv[1]))
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(private=True)
    manager = None
    receiver = None
    plugin = "nobamacro-shortcuts-" + uuid.uuid4().hex
    token = uuid.uuid4().hex
    loop = GLib.MainLoop()
    directory = tempfile.TemporaryDirectory(prefix="nobamacro-shortcuts-")
    try:
        if not bus.name_has_owner("org.kde.KWin"):
            raise RuntimeError("Global shortcuts require KDE Plasma. Window shortcuts still work.")
        kwin_owner = str(bus.get_name_owner("org.kde.KWin"))

        class Receiver(dbus.service.Object):
            @dbus.service.method("org.nobamacro.Shortcuts", in_signature="ss", out_signature="",
                                 sender_keyword="sender")
            def Action(self, received_token, action, sender=None):
                if str(sender) != kwin_owner or str(received_token) != token:
                    return
                if action in bindings:
                    emit("shortcut", action=str(action))
                elif action == "ready":
                    emit("shortcuts_ready")
                elif action == "failed":
                    emit("shortcuts_error", text="KDE could not register a shortcut. Choose another function key.")

        receiver = Receiver(bus, "/org/nobamacro/Shortcuts")
        manager = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting", introspect=False),
                                 "org.kde.kwin.Scripting")
        script = """
var bindings = BINDINGS;
var failed = false;
function notify(action) {
    callDBus(SERVICE, '/org/nobamacro/Shortcuts', 'org.nobamacro.Shortcuts', 'Action', TOKEN, action);
}
function bind(action, title) {
    var key = bindings[action];
    var ok = registerShortcut('NobaMacro_' + action + '_' + key,
                              'NobaMacro: ' + title, key, function() { notify(action); });
    if (ok === false) failed = true;
}
bind('record', 'Record / Stop');
bind('stop', 'Stop');
bind('play', 'Play');
notify(failed ? 'failed' : 'ready');
""".replace("BINDINGS", json.dumps(bindings)).replace("SERVICE", json.dumps(str(bus.get_unique_name()))).replace("TOKEN", json.dumps(token))
        path = Path(directory.name) / "shortcuts.js"
        path.write_text(script, encoding="utf-8")
        script_id = int(manager.loadScript(str(path), plugin, signature="ss", timeout=3))
        if script_id < 0:
            raise RuntimeError("KDE could not load the shortcut script")
        for object_path in (f"/Scripting/Script{script_id}", f"/{script_id}"):
            try:
                runner = dbus.Interface(bus.get_object("org.kde.KWin", object_path, introspect=False),
                                        "org.kde.kwin.Script")
                runner.run(signature="", timeout=3)
                break
            except dbus.exceptions.DBusException as exc:
                if exc.get_dbus_name() not in ("org.freedesktop.DBus.Error.UnknownObject", "org.freedesktop.DBus.Error.UnknownMethod"):
                    raise
        else:
            raise RuntimeError("KDE shortcut script object is unavailable")

        def watch_parent():
            sys.stdin.readline()  # Quit message or EOF; neither contains keyboard input.
            GLib.idle_add(loop.quit)

        threading.Thread(target=watch_parent, daemon=True).start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), False)[1])
        loop.run()
    finally:
        if manager is not None:
            with contextlib.suppress(Exception):
                manager.unloadScript(plugin, signature="s", timeout=3)
        if receiver is not None:
            receiver.remove_from_connection()
        bus.close()
        directory.cleanup()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        emit("shortcuts_error", text=str(exc))
        sys.exit(1)
