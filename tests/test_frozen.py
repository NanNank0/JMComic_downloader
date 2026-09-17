"""
Verify the FROZEN web app: launch the packaged binary, drive its HTTP API, and confirm
a real download completes with PDF export. This is the end-to-end proof that the
packaged product works, not just that it imports.
"""
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else (PROJECT_ROOT / "dist" / "jmcomic-downloader.exe")


def main():
    print(f"binary: {EXE}  ({EXE.stat().st_size / 1048576:.1f} MB)")

    # A --windowed build has no stdout, so get the URL from a file instead.
    url_path = Path(tempfile.gettempdir()) / "jm-frozen-url.txt"
    url_path.unlink(missing_ok=True)

    proc = subprocess.Popen([str(EXE), "--no-browser", "--port", "0",
                             "--url-file", str(url_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace")
    url = None
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"frozen app exited early: {proc.returncode}")
        if url_path.exists():
            text = url_path.read_text(encoding="utf-8").strip()
            if text.startswith("http"):
                url = text
                break
        time.sleep(0.5)
    if url is None:
        proc.kill()
        raise SystemExit("the frozen app never wrote its URL")

    base, token = url.split("/?token=")
    base = base.rstrip("/")
    print("serving:", url)

    try:
        with urllib.request.urlopen(base + "/", timeout=30) as r:
            html = r.read().decode("utf-8")
        print(f"GET /            -> {len(html)} bytes, form={'车号' in html}")

        with urllib.request.urlopen(f"{base}/api/config?token={token}", timeout=30) as r:
            cfg = json.loads(r.read().decode("utf-8"))
        print(f"GET /api/config  -> backend={cfg['backend']} py={cfg['python']} "
              f"missingDeps={cfg['missingDeps']}")

        events = []
        stop = threading.Event()

        def listen():
            req = urllib.request.Request(f"{base}/api/events?token={token}")
            with urllib.request.urlopen(req, timeout=200) as r:
                for raw in r:
                    if stop.is_set():
                        return
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

        threading.Thread(target=listen, daemon=True).start()
        time.sleep(0.5)

        tmp = tempfile.mkdtemp(prefix="jm-frozen-")
        body = json.dumps({"ids": "438696", "kind": "photo", "exports": ["pdf"],
                           "saveDir": tmp, "threads": 4,
                           "backend": cfg["backend"]}).encode("utf-8")
        req = urllib.request.Request(f"{base}/api/download?token={token}", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"POST download    -> {r.status} {json.loads(r.read().decode())}")

        deadline = time.time() + 240
        while time.time() < deadline and not any(e.get("type") == "done" for e in events):
            time.sleep(2)

        kinds = {}
        for e in events:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
        results = [e["item"] for e in events if e.get("type") == "result"]
        files = [p for p in Path(tmp).rglob("*") if p.is_file()]
        pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
        print(f"SSE              -> {kinds}")
        if results:
            print(f"result           -> JM{results[0]['id']} "
                  f"images={results[0].get('imageCount')} "
                  f"exports={len(results[0].get('exportFiles') or [])}")
        print(f"files            -> {len(files)} files, {len(pdfs)} pdf")

        ok = bool(results and results[0].get("imageCount") and pdfs
                  and any(e.get("type") == "done" and e.get("text") == "全部完成"
                          for e in events))
        print()
        print("FROZEN WEB APP:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
