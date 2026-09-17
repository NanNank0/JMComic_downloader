#!/usr/bin/env python3
"""
JMComic downloader - local web UI.

Instead of a native GUI toolkit, this serves a small page on 127.0.0.1 and opens the
user's browser. That choice removes the entire native graphics stack (Kivy/SDL2) from
the product, which is what made frozen desktop builds fail and macOS CI fragile: with
no GUI toolkit, packaging is just Python plus jmcomic.

All download work is delegated to `jmcore`, the same engine the CLI and the agent
skill use.

    python webui/server.py                 # serve, open the browser
    python webui/server.py --no-browser    # just serve (print the URL)
    python webui/server.py --port 8765

Security model
--------------
* Binds 127.0.0.1 only.
* Requires a random per-run token on every request, so another process (or a web page
  the user has open, via a blind POST to localhost) cannot drive this one.
* Validates the Host header to blunt DNS-rebinding.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent / "scripts", _HERE.parent):
    if (_candidate / "jmcore.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import jmcore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"jmcore.py not found near {_HERE}: {exc}") from exc

try:
    from ui import INDEX_HTML
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"ui.py not found beside server.py: {exc}") from exc


# --------------------------------------------------------------------------- #
# job state and event fan-out
# --------------------------------------------------------------------------- #

class Hub:
    """Broadcasts progress events to every connected browser."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._backlog: list[dict] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            for event in self._backlog:
                q.put(event)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            # Keep a short replay buffer so a browser that connects mid-download still
            # sees recent progress instead of an empty pane.
            if event.get("type") in ("log", "result"):
                self._backlog.append(event)
                del self._backlog[:-200]
            for q in self._subscribers:
                q.put(event)

    def reset_backlog(self) -> None:
        with self._lock:
            self._backlog.clear()


class Job:
    """One download run. Only one may be active at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


HUB = Hub()
JOB = Job()


def _run_job(settings_payload: dict) -> None:
    """Worker: download each id in turn so cancel can act between them."""
    log_handler = None
    try:
        jmcomic = jmcore.load_jmcomic()
        log_handler = jmcore.attach_log_sink(
            lambda text: HUB.publish({"type": "log", "text": text}))

        ids = settings_payload["ids"]
        export_suffixes = [jmcore.EXPORT_KINDS[s][1] for s in settings_payload["exports"]]

        completed = 0
        for jm_id in ids:
            if JOB.cancel.is_set():
                HUB.publish({"type": "log", "text": "已取消，停止后续任务"})
                break

            settings = jmcore.DownloadSettings(
                targets=[jm_id],
                kind=settings_payload["kind"],
                exports=settings_payload["exports"],
                save_dir=settings_payload["saveDir"],
                threads=settings_payload["threads"],
                proxy=settings_payload["proxy"],
                http_backend=settings_payload["backend"],
                dir_rule="Bd / Aid / Ptitle",
            )
            try:
                payload = jmcore.run_download(settings, jmcomic=jmcomic,
                                              progress_sink=lambda t: HUB.publish(
                                                  {"type": "log", "text": t}))
            except jmcore.OperationError as e:
                HUB.publish({"type": "error", "text": e.message if not e.hint
                             else f"{e.message}（{e.hint}）"})
                continue

            HUB.publish({"type": "result", "item": payload})
            completed += 1

        if JOB.cancel.is_set():
            HUB.publish({"type": "done", "text": f"已取消（完成 {completed} 个）"})
        else:
            HUB.publish({"type": "done", "text": "全部完成"})

    except Exception as e:
        HUB.publish({"type": "log", "text": traceback.format_exc()})
        HUB.publish({"type": "error", "text": f"{type(e).__name__}: {e}"})
        HUB.publish({"type": "done", "text": "失败"})
    finally:
        if log_handler is not None:
            jmcore.detach_log_sink(log_handler)
        with JOB.lock:
            JOB.thread = None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    server_version = "jmcomic-downloader"
    protocol_version = "HTTP/1.1"
    token = ""
    allowed_hosts: set[str] = set()

    # -- helpers -------------------------------------------------------- #

    def log_message(self, fmt, *args):  # quieter than the default stderr spam
        pass

    def _authorised(self, query: dict) -> bool:
        supplied = (query.get("token") or [""])[0]
        if not secrets.compare_digest(str(supplied), self.token):
            self._json({"error": "invalid or missing token"}, 403)
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host and host not in self.allowed_hosts:
            self._json({"error": "unexpected Host header"}, 403)
            return False
        return True

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # -- routes --------------------------------------------------------- #

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            # The per-run token is injected into the page rather than required in the
            # URL: python-for-android's webview bootstrap loads a fixed
            # "http://127.0.0.1:PORT/" with no query string, so a URL-only token would
            # leave the Android UI unable to call its own API. API requests still carry
            # the token, so another local process still cannot drive this one.
            body = INDEX_HTML.replace("__TOKEN__", self.token).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if not self._authorised(query):
            return

        if parsed.path == "/api/config":
            self._json(_config_payload())
        elif parsed.path == "/api/events":
            self._events()
        elif parsed.path == "/api/status":
            self._json({"running": JOB.running})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._authorised(query):
            return

        if parsed.path == "/api/download":
            self._start_download(self._body())
        elif parsed.path == "/api/cancel":
            JOB.cancel.set()
            self._json({"ok": True})
        elif parsed.path == "/api/shutdown":
            self._json({"ok": True})
            HUB.publish({"type": "log", "text": "正在退出…"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"error": "not found"}, 404)

    # -- handlers ------------------------------------------------------- #

    def _events(self):
        """Server-Sent Events stream of job progress."""
        q = HUB.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    # Comment frame keeps proxies and the browser from timing out.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                data = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)

    def _start_download(self, body: dict):
        if JOB.running:
            self._json({"error": "已有任务在下载中，请先等待或取消"}, 409)
            return

        raw_ids = body.get("ids") or ""
        jmcomic = None
        try:
            jmcomic = jmcore.load_jmcomic()
        except jmcore.OperationError as e:
            self._json({"error": e.message}, 500)
            return

        parsed, bad = [], []
        for token in jmcore.split_id_text(str(raw_ids)):
            try:
                parsed.append(jmcore.require_id(jmcomic, token))
            except jmcore.OperationError:
                bad.append(token)
        if bad:
            HUB.publish({"type": "log",
                         "text": f"[跳过] 无法识别的车号：{', '.join(bad)}"})
        if not parsed:
            self._json({"error": "没有可用的车号"}, 400)
            return

        exports = [s for s in (body.get("exports") or []) if s in jmcore.EXPORT_KINDS]
        settings_payload = {
            "ids": parsed,
            "kind": "photo" if body.get("kind") == "photo" else "album",
            "exports": exports,
            "saveDir": (body.get("saveDir") or "").strip() or str(jmcore.default_download_dir()),
            "threads": max(1, min(50, int(body.get("threads") or 30))),
            "proxy": (body.get("proxy") or "").strip(),
            "backend": body.get("backend") or jmcore.default_http_backend(),
        }

        JOB.cancel.clear()
        HUB.reset_backlog()
        JOB.thread = threading.Thread(target=_run_job, args=(settings_payload,), daemon=True)
        JOB.thread.start()
        self._json({"ok": True, "ids": parsed})


def _config_payload() -> dict:
    missing = []
    for suffix in jmcore.EXPORT_KINDS:
        for entry in jmcore.missing_export_dependencies([suffix]):
            missing.append(entry.split(":", 1)[-1])
    return {
        "saveDir": str(jmcore.default_download_dir()),
        "backend": jmcore.default_http_backend(),
        "android": jmcore.is_android(),
        "missingDeps": sorted(set(missing)),
        "python": sys.version.split()[0],
    }


# --------------------------------------------------------------------------- #
# startup
# --------------------------------------------------------------------------- #

def pick_port(preferred: int = 0) -> int:
    """Return a free port, preferring `preferred` when it is available."""
    for candidate in ([preferred] if preferred else []) + [0]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise SystemExit("could not bind a local port")


ANDROID_WEBVIEW_PORT = 5000
"""python-for-android's webview bootstrap pings and loads this port by default."""


def serve(port: int = 0, open_browser: bool = True, quiet: bool = False,
          url_file: str | None = None) -> int:
    jmcore.reconfigure_streams_utf8()
    jmcore.route_logs_to_stderr()

    android = jmcore.is_android()
    if android:
        # On Android the UI is a WebView inside our own activity, not the user's
        # browser, and p4a hardcodes the port it will load. Bind exactly that.
        port = port or ANDROID_WEBVIEW_PORT
        open_browser = False

    token = secrets.token_urlsafe(24)
    try:
        chosen = pick_port(port)
    except SystemExit:
        if android:
            raise SystemExit(
                f"port {ANDROID_WEBVIEW_PORT} is unavailable, but the Android WebView "
                f"is hardcoded to load it")
        raise
    Handler.token = token
    Handler.allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}

    httpd = ThreadingHTTPServer(("127.0.0.1", chosen), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{chosen}/?token={token}"

    # A --windowed bundle has sys.stdout set to None, so printing the URL is not
    # enough to find it. Writing it to a file makes a packaged build drivable by a
    # script (and recoverable by a user if the browser did not open).
    if url_file:
        try:
            Path(url_file).expanduser().write_text(url + "\n", encoding="utf-8")
        except OSError as e:
            print(f"could not write {url_file}: {e}", file=sys.stderr)

    if not quiet:
        print("JMComic 下载器已启动")
        print(f"  地址: {url}")
        if url_file:
            print(f"  地址也已写入: {url_file}")
        print("  停止: 在此窗口按 Ctrl+C（或点页面里的「退出程序」）")
        print()

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        if url_file:
            try:
                Path(url_file).expanduser().unlink()
            except OSError:
                pass
    return 0


def _selftest(album_id: str = "438696") -> int:
    """
    Non-GUI verification for packaged builds: prove the bundle can import jmcomic,
    reach the site and write files.

    The result is written to a file as well as stdout, because a frozen --windowed
    build has sys.stdout set to None and `print` would silently discard everything.
    `gui/build.py --verify` reads that file back.
    """
    import tempfile

    report = Path(tempfile.gettempdir()) / "jmcomic-downloader-selftest.log"
    lines: list[str] = []

    def emit(text=""):
        lines.append(str(text))
        try:
            if sys.stdout is not None:
                print(text)
        except Exception:
            pass
        try:
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass
        return text

    def finish(code: int, summary: str) -> int:
        emit(summary)
        return code

    jmcore.route_logs_to_stderr()
    emit(f"android={jmcore.is_android()} platform={sys.platform}")
    emit(f"http backend={jmcore.default_http_backend()} python={sys.version.split()[0]}")
    emit(f"report={report}")

    try:
        jmcomic = jmcore.load_jmcomic()
    except jmcore.OperationError as e:
        return finish(1, f"FAIL import: {e.message}")
    emit(f"jmcomic {getattr(jmcomic, '__version__', '?')} from {jmcomic.__file__}")

    # The UI is embedded, so a frozen build either has it or does not.
    emit(f"ui embedded={len(INDEX_HTML) > 1000} bytes={len(INDEX_HTML)}")

    with tempfile.TemporaryDirectory(prefix="jm-selftest-") as tmp:
        settings = jmcore.DownloadSettings(
            targets=[album_id],
            kind="photo",
            exports=["pdf"],
            save_dir=tmp,
            threads=4,
            http_backend=jmcore.default_http_backend(),
        )
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic,
                                          progress_sink=emit)
        except Exception as e:
            return finish(1, f"FAIL download: {type(e).__name__}: {e}")

        item = payload
        emit(f"downloaded id={item.get('id')} images={item.get('imageCount')} "
             f"duration={item.get('durationSec')}s")
        emit(f"savePath={item.get('savePath')}")
        for path in item.get("exportFiles") or []:
            emit(f"export={path}")
        if not item.get("imageCount"):
            return finish(1, "FAIL: no images were written")

    return finish(0, "SELFTEST OK")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the JMComic downloader UI on localhost.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=0,
                        help="Port to bind (0 = pick a free one).")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser automatically.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the banner.")
    parser.add_argument("--url-file", default=None, metavar="PATH",
                        help="Write the UI URL here. Needed for --windowed builds, "
                             "which have no console to print it to.")
    parser.add_argument("--selftest", nargs="?", const="438696", default=None,
                        metavar="ALBUM_ID",
                        help="Verify a packaged build by downloading one chapter, then exit.")
    args = parser.parse_args(argv)

    if args.selftest:
        try:
            return _selftest(args.selftest)
        except Exception:
            detail = traceback.format_exc()
            try:
                (Path(__import__("tempfile").gettempdir())
                 / "jmcomic-downloader-error.log").write_text(detail, encoding="utf-8")
            except Exception:
                pass
            print(detail, file=sys.stderr)
            return 1

    return serve(port=args.port, open_browser=not args.no_browser, quiet=args.quiet,
                 url_file=args.url_file)


if __name__ == "__main__":
    sys.exit(main())
