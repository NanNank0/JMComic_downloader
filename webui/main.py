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
    print(f"[jmcomic] default download dir: {jmcore.default_download_dir()}", flush=True)
except Exception:
    # Not fatal for serving the UI, but worth seeing in logcat.
    print("[jmcomic] WARNING: jmcore probe failed", flush=True)
    traceback.print_exc()


def main() -> int:
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
