#!/usr/bin/env python3
"""
Entry point for the Android build (python-for-android).

Why this file must exist
------------------------
p4a's `webview` bootstrap does NOT require an entry point at build time - its build
script literally skips the `main.py` check:

    if (get_bootstrap_name() != "sdl" or args.launcher is None) and \
            get_bootstrap_name() not in ["webview", "service_library"]:
        # (webview doesn't need an entrypoint, apparently)
        ...require main.py...

But at RUNTIME `PythonActivity` still launches `main.py` from the app directory. So a
webview app without `main.py` builds successfully and then does nothing: the Java side
pings `localhost:<port>` in an endless loop and the WebView stays on its loading page
forever. That failure mode cost a real debugging round.

The port is not configurable here on purpose: p4a's `WebViewLoader` is generated with a
fixed port (default 5000) and loads `http://127.0.0.1:5000/`. We bind exactly that.

On desktop this file is harmless and unused - `webui/server.py` is the normal entry.
"""

from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The app directory must be importable so `import server` / `import jmcore` resolve.
# jmcore.py is staged here by gui/build_android.py.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# p4a renders Python stdout/stderr into logcat, so these lines are how a failure is
# diagnosed on-device (adb logcat -s python:D).
print("[jmcomic] main.py starting", flush=True)
print(f"[jmcomic] app dir: {HERE}", flush=True)
print(f"[jmcomic] files: {sorted(p.name for p in HERE.iterdir())}", flush=True)

try:
    import server
except Exception:
    print("[jmcomic] FATAL: could not import server", flush=True)
    traceback.print_exc()
    raise

try:
    import jmcore

    print(f"[jmcomic] android={jmcore.is_android()} "
          f"backend={jmcore.default_http_backend()}", flush=True)
    # Printed so a device-side failure report shows exactly which signal matched.
    print(f"[jmcomic] android signals: {jmcore.android_signals()}", flush=True)

    # MUST run on this (main) thread: p4a patches ctypes.util to import the `android`
    # module, which needs the Activity's ClassLoader - available here, but not on the
    # worker thread that performs downloads. See the function's docstring.
    print(f"[jmcomic] ctypes.util: {jmcore.ensure_ctypes_util_importable()}", flush=True)

    # JM serves its page images as .webp, and on Android Pillow's WebP codec only
    # exists if the build included the `libwebp` recipe (see buildozer.spec). Without
    # it every download "succeeds" and then fails to decode, so print the truth here.
    print(f"[jmcomic] {jmcore.pillow_codecs()}", flush=True)

    # Where do downloads go? p4a's default (<ANDROID_PRIVATE>) is invisible to every
    # file manager and to USB/MTP, so prefer the app's EXTERNAL files directory, which
    # needs no permission and the user can actually open. Must also run on this thread:
    # it is the one jnius caller, and PythonActivity is an app class.
    print(f"[jmcomic] storage probe: {jmcore.android_storage_probe()}", flush=True)
    print(f"[jmcomic] default download dir: {jmcore.default_download_dir()}", flush=True)
except Exception:
    # Not fatal for serving the UI, but worth seeing in logcat.
    print("[jmcomic] WARNING: jmcore probe failed", flush=True)
    traceback.print_exc()


def _migrate_existing_downloads() -> None:
    """
    Move albums downloaded by older versions into the now-browsable directory.

    Runs off the main thread because `serve()` never returns and a large old download
    directory must not delay the UI. It is pure file I/O - no jnius - so that is safe.
    """
    try:
        print(f"[jmcomic] migrate: {jmcore.migrate_downloads()}", flush=True)
    except Exception:
        print("[jmcomic] migrate failed", flush=True)
        traceback.print_exc()


def main() -> int:
    threading.Thread(target=_migrate_existing_downloads, daemon=True).start()
    # quiet=True: there is no console; the banner would go nowhere useful.
    # open_browser=False: the WebView is the UI.
    return server.serve(port=server.ANDROID_WEBVIEW_PORT, open_browser=False, quiet=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        print("[jmcomic] FATAL: server did not start", flush=True)
        traceback.print_exc()
        # Re-raise so PythonActivity reports it instead of silently idling forever.
        raise
