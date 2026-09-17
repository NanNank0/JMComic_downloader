"""
End-to-end test of the web UI: start the real server, drive the real API, and confirm
a download completes and reports through the SSE stream.
"""
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER = PROJECT_ROOT / "webui" / "server.py"


def start_server():
    p = subprocess.Popen(
        [sys.executable, "-u", str(SERVER), "--no-browser", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace")
    url = None
    deadline = time.time() + 40
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                raise SystemExit(f"server exited early: {p.returncode}")
            continue
        m = re.search(r"(http://127\.0\.0\.1:\d+/\?token=\S+)", line)
        if m:
            url = m.group(1)
            break
    if url is None:
        p.kill()
        raise SystemExit("could not read the server URL")

    # CRITICAL: keep draining the child's stdout. jmcomic logs a line per image, and
    # an undrained pipe fills up; the child then blocks inside its logging handler,
    # which stalls the very download threads that log. That deadlock is a test-harness
    # artefact - a real user runs this in a console or with no console at all.
    def drain():
        try:
            for _ in p.stdout:
                pass
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True).start()
    return p, url


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read().decode("utf-8")


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    proc, url = start_server()
    base, token = url.split("/?token=")
    base = base.rstrip("/")
    print("server:", url)

    try:
        # 1. the page itself needs no token
        status, html = get(base + "/")
        print(f"GET /              -> {status}, {len(html)} bytes, "
              f"has form: {'车号' in html}, has quit button: {'退出程序' in html}")

        # 2. token enforcement
        try:
            get(base + "/api/config?token=wrong")
            print("GET bad token      -> !!! accepted, should have been rejected")
        except urllib.error.HTTPError as e:
            print(f"GET bad token      -> {e.code} (rejected, good)")

        # 3. config
        status, body = get(f"{base}/api/config?token={token}")
        cfg = json.loads(body)
        print(f"GET /api/config    -> {status} backend={cfg['backend']} "
              f"android={cfg['android']} saveDir set={bool(cfg['saveDir'])}")

        # 4. subscribe to SSE in the background
        events = []
        stop = threading.Event()

        def listen():
            req = urllib.request.Request(f"{base}/api/events?token={token}")
            with urllib.request.urlopen(req, timeout=180) as r:
                for raw in r:
                    if stop.is_set():
                        return
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        time.sleep(0.5)

        # 5. a bad id must be rejected cleanly
        code, body = post_json(f"{base}/api/download?token={token}",
                               {"ids": "not-an-id", "kind": "photo"})
        print(f"POST bad id        -> {code} {body.get('error', '')}")

        # 6. real download
        import tempfile
        tmp = tempfile.mkdtemp(prefix="jm-web-")
        code, body = post_json(f"{base}/api/download?token={token}",
                               {"ids": "438696", "kind": "photo", "exports": ["pdf"],
                                "saveDir": tmp, "threads": 4, "backend": "requests"})
        print(f"POST /api/download -> {code} ids={body.get('ids')}")

        deadline = time.time() + 240
        last = 0
        while time.time() < deadline:
            if any(e.get("type") == "done" for e in events):
                break
            if len(events) != last:
                last = len(events)
                tail = events[-1]
                print(f"   ... {len(events)} events, last={tail.get('type')} "
                      f"{str(tail.get('text') or tail.get('item', {}).get('id'))[:70]}")
            time.sleep(3)

        kinds = {}
        for e in events:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        print(f"SSE events         -> {kinds}")
        results = [e["item"] for e in events if e.get("type") == "result"]
        if results:
            it = results[0]
            print(f"result             -> JM{it['id']} images={it.get('imageCount')} "
                  f"exports={len(it.get('exportFiles') or [])} "
                  f"duration={it.get('durationSec')}s")
        done = [e["text"] for e in events if e.get("type") == "done"]
        print(f"done               -> {done}")

        files = [p for p in Path(tmp).rglob("*") if p.is_file()]
        pdfs = [p.name for p in files if p.suffix.lower() == ".pdf"]
        print(f"files on disk      -> {len(files)} files, {len(pdfs)} pdf")

        # 7. shutdown endpoint
        code, body = post_json(f"{base}/api/shutdown?token={token}", {})
        print(f"POST /api/shutdown -> {code} {body}")
        try:
            proc.wait(timeout=20)
            print(f"server exited      -> code {proc.returncode}")
        except subprocess.TimeoutExpired:
            print("server did NOT exit after shutdown !!!")

        ok = (results and results[0].get("imageCount") and pdfs
              and done == ["全部完成"])
        print()
        print("WEB UI TEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
