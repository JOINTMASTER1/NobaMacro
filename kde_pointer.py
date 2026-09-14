"""Read the real KDE Wayland cursor in the desktop user's session.

Each probe is a temporary, read-only KWin script. No plugin is installed or enabled.
The privileged input helper never connects to the desktop session bus.
"""
import contextlib
import json
import tempfile
import time
import sys
import traceback
import uuid
from pathlib import Path

from macro_core import validate_position


class KDEPointer:
    def __init__(self):
        try:
            import dbus
            import dbus.service
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except ImportError as exc:
            raise RuntimeError("Automatic positioning needs python3-dbus and python3-gobject. See the updated README.") from exc
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus(private=True)
        self.manager = None
        self.service = None
        self.temp = None
        self.plugin = "nobamacro-probe-" + uuid.uuid4().hex
        self.dbus = dbus
        self.context = GLib.MainContext.default()
        self.result = None
        self.token = None
        owner = self

        class Receiver(dbus.service.Object):
            @dbus.service.method("org.nobamacro.Pointer", in_signature="ss", out_signature="",
                                 sender_keyword="sender")
            def Report(self, token, payload, sender=None):
                if str(sender) == owner.kwin_owner and str(token) == owner.token:
                    owner.result = str(payload)

        try:
            if not self.bus.name_has_owner("org.kde.KWin"):
                raise RuntimeError("Automatic positioning requires KDE Plasma/KWin. Turn it off for manual positioning on another desktop.")
            self.kwin_owner = str(self.bus.get_name_owner("org.kde.KWin"))
            self.manager = dbus.Interface(self.bus.get_object("org.kde.KWin", "/Scripting", introspect=False),
                                          "org.kde.kwin.Scripting")
            self.service = Receiver(self.bus, "/org/nobamacro/Pointer")
            self.temp = tempfile.TemporaryDirectory(prefix="nobamacro-pointer-")
        except Exception:
            self.close()
            raise

    def get(self):
        self.token = uuid.uuid4().hex
        self.result = None
        service = json.dumps(str(self.bus.get_unique_name()))
        token = json.dumps(self.token)
        script = """
var p = workspace.cursorPos;
var monitors = workspace.screens;
var rectangles = [];
for (var i = 0; i < monitors.length; ++i) {
    var g = monitors[i].geometry;
    rectangles.push({x: Math.round(g.x), y: Math.round(g.y),
                     width: Math.round(g.width), height: Math.round(g.height)});
}
callDBus(SERVICE, '/org/nobamacro/Pointer', 'org.nobamacro.Pointer', 'Report', TOKEN,
         JSON.stringify({x: Math.round(p.x), y: Math.round(p.y), screens: rectangles}));
""".replace("SERVICE", service).replace("TOKEN", token)
        path = Path(self.temp.name) / "probe.js"
        path.write_text(script, encoding="utf-8")
        loaded = False
        stage = "loading cursor script"
        try:
            # Qt exports both loadScript(s) and loadScript(ss). dbus-python's
            # introspection map can retain only the one-argument overload.
            # Specify the actual wire signature instead of inferring it.
            script_id = int(self.manager.loadScript(str(path), self.plugin, signature="ss", timeout=3))
            if script_id < 0:
                raise RuntimeError("KWin could not load the temporary pointer probe")
            loaded = True
            stage = "running cursor script"
            # Plasma 6 uses /Scripting/ScriptN. /N is the older Plasma 5 path.
            for object_path in (f"/Scripting/Script{script_id}", f"/{script_id}"):
                try:
                    runner = self.dbus.Interface(self.bus.get_object("org.kde.KWin", object_path, introspect=False),
                                                 "org.kde.kwin.Script")
                    runner.run(signature="", timeout=3)
                    break
                except self.dbus.exceptions.DBusException as exc:
                    if exc.get_dbus_name() not in ("org.freedesktop.DBus.Error.UnknownObject",
                                                   "org.freedesktop.DBus.Error.UnknownMethod"):
                        raise
            else:
                raise RuntimeError("Neither supported KWin script object path is available")
            stage = "receiving cursor position"
            deadline = time.monotonic() + 3
            while self.result is None and time.monotonic() < deadline:
                # Dispatch only the D-Bus GLib context, never Tk from this worker.
                self.context.iteration(False)
                time.sleep(0.002)
            if self.result is None:
                raise RuntimeError("KWin did not report the cursor position. Check that the session is KDE Plasma 6.")
            return validate_position(json.loads(self.result))
        except Exception as exc:
            raise RuntimeError(f"KDE pointer probe ({stage}): {exc}") from exc
        finally:
            if loaded:
                with contextlib.suppress(Exception):
                    self.manager.unloadScript(self.plugin, signature="s", timeout=3)

    def close(self):
        if self.manager is not None:
            with contextlib.suppress(Exception):
                self.manager.unloadScript(self.plugin, signature="s", timeout=3)
        if self.service is not None:
            self.service.remove_from_connection()
        self.bus.close()
        if self.temp is not None:
            self.temp.cleanup()


def serve():
    pointer = None
    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if command.get("type") == "quit":
                    break
                if command.get("type") != "get":
                    raise ValueError("Unknown cursor request")
                if pointer is None:
                    pointer = KDEPointer()
                print(json.dumps({"position": pointer.get()}), flush=True)
            except Exception as exc:
                traceback.print_exc()
                print(json.dumps({"error": str(exc)}), flush=True)
    finally:
        if pointer is not None:
            pointer.close()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        pointer = KDEPointer()
        try:
            print(json.dumps(pointer.get(), indent=2))
        finally:
            pointer.close()
