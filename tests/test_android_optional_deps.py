#!/usr/bin/env python3
"""
Prove the app works in an Android-like environment where optional C extensions are
missing.

Android cannot install three things this project touches:

  * `curl_cffi` - jmcomic imports it at MODULE scope
    (`jmcomic/jm_async_client.py: from curl_cffi.requests import AsyncSession`, eagerly
    reached from `jmcomic/__init__.py`), and python-for-android has no recipe for it.
  * `pyyaml` - only platform-specific/ABI wheels exist, so p4a's resolver rejects it.
  * `img2pdf` - it hard-depends on `pikepdf` (a QPDF binding) which has no android wheel
    and no p4a recipe, so it cannot be installed either.

JSON and ZIP export still work, and PDF is produced by `jmcore` through Pillow instead
of img2pdf, so no feature is actually lost on Android.

"Not needed" must be *proved*, not assumed: an earlier version of this project claimed
curl_cffi was imported lazily, and that was wrong - the Android build failed on device
with `No module named 'curl_cffi'`.

This test makes the claim falsifiable. It:

  1. sets the env vars p4a sets, so `jmcore.is_android()` reports True;
  2. installs an import hook that refuses curl_cffi / yaml / img2pdf;
  3. loads the engine (which must stub curl_cffi to satisfy jmcomic's import);
  4. checks the stub fails *loudly* if the async client is ever used;
  5. runs a real download with PDF export and validates the produced file.

Run from the project root:  python tests/test_android_optional_deps.py
Needs network.
"""

from __future__ import annotations

import importlib.abc
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "webui"))

# Modules Android cannot provide. `ruamel.yaml` is a different package and unaffected.
BLOCKED = ("curl_cffi", "yaml", "img2pdf")


class BlockModules(importlib.abc.MetaPathFinder):
    """Refuse the named modules, simulating the Android environment."""

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED:
            raise ImportError(f"blocked for this test: {fullname} (unavailable on Android)")
        return None


def main() -> int:
    # p4a sets these; jmcore.is_android() keys off them.
    private = tempfile.mkdtemp(prefix="jm-android-sim-")
    os.environ["ANDROID_PRIVATE"] = private
    os.environ["ANDROID_ARGUMENT"] = private
    print(f"simulating Android (ANDROID_PRIVATE={private})")

    sys.meta_path.insert(0, BlockModules())
    for name in BLOCKED:
        try:
            __import__(name)
            print(f"FAIL: the block for {name} is not working; test would be meaningless")
            return 1
        except ImportError:
            pass
    print(f"blocked: {', '.join(BLOCKED)}")

    import jmcore  # noqa: E402  (import after the hook is installed)

    if not jmcore.is_android():
        print("FAIL: is_android() is False, so this does not simulate a device")
        return 1
    print(f"is_android() = True, default backend = {jmcore.default_http_backend()}")
    if jmcore.default_http_backend() != jmcore.ANDROID_HTTP_BACKEND:
        print("FAIL: Android must default to the requests backend")
        return 1

    # 1. The import that used to blow up on device.
    try:
        jmcomic = jmcore.load_jmcomic()
    except Exception as exc:
        print(f"FAIL: load_jmcomic() raised: {type(exc).__name__}: {exc}")
        return 1
    print(f"jmcomic {jmcomic.__version__} imported with curl_cffi unavailable")
    if not jmcore.CURL_CFFI_STUBBED:
        print("WARNING: no stub was installed (real curl_cffi present?)")

    # 2. The stub must fail loudly, not silently, if the async client is used.
    import curl_cffi  # noqa: E402  (resolves to the stub)

    try:
        curl_cffi.requests.AsyncSession()
        print("FAIL: the stub allowed an async session to be created")
        return 1
    except RuntimeError as exc:
        print(f"stub refuses async use as intended: {str(exc)[:70]}...")

    # 3. What the web UI asks for at startup.
    import server  # noqa: E402

    cfg = server._config_payload()
    print(f"web UI config payload OK: backend={cfg['backend']} android={cfg['android']}")
    if cfg["backend"] != "requests":
        print("FAIL: UI would advertise the wrong backend on Android")
        return 1

    # 4. A real download with PDF export, through the same engine/paths Android uses.
    with tempfile.TemporaryDirectory(prefix="jm-android-sim-dl-") as tmp:
        settings = jmcore.DownloadSettings(
            targets=["438696"],
            kind="photo",
            exports=["pdf"],                 # must work via Pillow, not img2pdf
            save_dir=tmp,
            threads=4,
            http_backend=jmcore.default_http_backend(),
        )
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic)
        except Exception as exc:
            print(f"FAIL: download in simulated Android: {type(exc).__name__}: {exc}")
            return 1

        files = [p for p in Path(tmp).rglob("*") if p.is_file()]
        print(f"download OK: images={payload.get('imageCount')} files={len(files)}")
        if not payload.get("imageCount"):
            print("FAIL: nothing downloaded")
            return 1

        # 5. PDF must be produced without img2pdf.
        pdfs = sorted(Path(tmp).glob("*.pdf"))
        if not pdfs:
            print("FAIL: no PDF produced (PDF export must not depend on img2pdf)")
            return 1
        head = pdfs[0].read_bytes()[:5]
        if head != b"%PDF-":
            print(f"FAIL: {pdfs[0].name} is not a valid PDF (magic={head!r})")
            return 1
        print(f"PDF OK without img2pdf: {pdfs[0].name} ({pdfs[0].stat().st_size / 1048576:.1f} MB)")

    print()
    print("ANDROID OPTIONAL DEPS: PASS — 缺 curl_cffi / pyyaml / img2pdf 也能下载并导出 PDF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
