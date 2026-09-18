#!/usr/bin/env python3
"""
Guard where Android downloads are written.

Background
----------
p4a gives the app `<ANDROID_PRIVATE>` (= `/data/user/0/<package>/files`) as its
writable home, and the first Android build wrote downloads to
`/data/user/0/<package>/files/downloads`. That path is real, the download really
succeeds, and **no file manager, USB/MTP transfer or `adb pull` can show it to the
user** - so the honest user report was "it says it finished, but I cannot find the
files anywhere".

The fix is to prefer the app's EXTERNAL files directory
(`<external>/Android/data/<package>/files/downloads`): it needs no permission on any
Android version, plain `open()` works, and the user can browse it from a file manager
and over USB. The private directory stays as a fallback, and `migrate_downloads()`
moves anything an older version already wrote.

What this test checks
---------------------
Everything except a real device call, which is why `android_storage_probe()` is split
from the write test it performs:

  * the package name is recovered from p4a's env vars
  * `default_download_dir()` follows the probed external root, and falls back to the
    private directory when the probe found nothing
  * `android_storage_probe()` only ever accepts a directory it has really written into,
    reports it, and never returns a path we cannot write
  * `migrate_downloads()` moves albums, never overwrites, is a no-op without a source
  * the Android entry point actually calls the probe (source-level check, like
    tests/test_android_requirements.py does for main.py)

Run from the project root:  python tests/test_android_storage.py
Exits non-zero on violation. Needs no network and no Android tooling.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANDROID_MAIN = PROJECT_ROOT / "webui" / "main.py"
PACKAGE = "io.github.nannank0.jmcomicdownloader"

# Pretend to be Android exactly the way p4a looks from inside the app: the signals
# jmcore.is_android() consults, and nothing else.
os.environ["ANDROID_PRIVATE"] = f"/data/user/0/{PACKAGE}/files"
os.environ["ANDROID_ARGUMENT"] = f"/data/user/0/{PACKAGE}/files/app"
os.environ["P4A_MINSDK"] = "24"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import jmcore  # noqa: E402  (after the env vars above, on purpose)

PROBLEMS = []


def check(condition: bool, description: str) -> None:
    if condition:
        print(f"  ok   {description}")
    else:
        print(f"  FAIL {description}")
        PROBLEMS.append(description)


def with_fresh_probe_state():
    """Run the body with the module's external-root cache cleared."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            jmcore._ANDROID_EXTERNAL_ROOT = None
            try:
                return func(*args, **kwargs)
            finally:
                jmcore._ANDROID_EXTERNAL_ROOT = None
        return wrapper
    return decorator


@with_fresh_probe_state()
def test_package_and_fallback(tmp: Path) -> None:
    print("package name / private fallback")
    check(jmcore.is_android(), "Android signals from the env vars are detected")
    check(jmcore._android_package_name() == PACKAGE,
          f"package name parsed from ANDROID_PRIVATE ({jmcore._android_package_name()})")
    check("downloads" in str(jmcore.default_download_dir()),
          "without a probe the private/downloads directory is still used")
    check(str(jmcore._android_private_root()).endswith("files"),
          "private root is p4a's app files directory")


@with_fresh_probe_state()
def test_probe_selects_only_writable(tmp: Path) -> None:
    print("probe only accepts a directory it can write into")

    # A real directory on this machine stands in for the device's external storage.
    external = tmp / "Android" / "data" / PACKAGE / "files"
    original = jmcore._candidate_external_roots
    jmcore._candidate_external_roots = lambda: [(external, "test double")]
    try:
        report = jmcore.android_storage_probe()
        check(jmcore.android_external_root() == external,
              "the writable candidate is selected")
        check(str(external / "downloads") in report,
              f"report names the browsable path ({report})")
        check(jmcore.default_download_dir() == external / "downloads",
              "default_download_dir() follows the probe")
        check((external / "downloads").is_dir(),
              "the download directory is created up front")
        check(jmcore.android_storage_probe().startswith("already set"),
              "a second probe is a no-op (idempotent)")
    finally:
        jmcore._candidate_external_roots = original

    # Now the failure path: nothing writable must NOT be reported as browsable.
    jmcore._ANDROID_EXTERNAL_ROOT = None
    blocker = tmp / "a-file-not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    jmcore._candidate_external_roots = lambda: [(blocker / "nested", "unwritable")]
    try:
        report = jmcore.android_storage_probe()
        check(jmcore.android_external_root() is None,
              "an unwritable candidate is rejected")
        check("NO file manager can show" in report,
              f"fallback is reported honestly ({report})")
        check(str(jmcore._android_private_root() / "downloads")
              == str(jmcore.default_download_dir()),
              "fallback default stays inside the app-private directory")
    finally:
        jmcore._candidate_external_roots = original


@with_fresh_probe_state()
def test_posix_guess_is_guarded(tmp: Path) -> None:
    print("guessed external paths")
    guessed = jmcore._candidate_external_roots()
    if os.name == "posix":
        check(any("/Android/data/" in str(path) for path, _ in guessed),
              "on POSIX the standard /sdcard-style paths are offered as guesses")
    else:
        check(not any("sdcard" in str(path) for path, _ in guessed),
              "on Windows no '/sdcard' guess is made (it would mean C:\\sdcard)")


@with_fresh_probe_state()
def test_migration(tmp: Path) -> None:
    print("migrating files written by an older version")
    source = tmp / "private" / "downloads"
    target = tmp / "external" / "downloads"
    (source / "董卓 上+下").mkdir(parents=True)
    (source / "董卓 上+下" / "00001.webp").write_bytes(b"img")
    (source / "keep-me").mkdir()
    # An entry that already exists at the destination must NOT be overwritten.
    (source / "already-there").mkdir()
    (source / "already-there" / "old.webp").write_bytes(b"old")
    (target / "already-there").mkdir(parents=True)
    (target / "already-there" / "new.webp").write_bytes(b"new")

    report = jmcore.migrate_downloads(source=source, target=target)
    check((target / "董卓 上+下" / "00001.webp").read_bytes() == b"img",
          "an album directory is moved into the browsable location")
    check(not (source / "董卓 上+下").exists(), "the private copy is gone afterwards")
    check((target / "keep-me").is_dir(), "an empty directory is moved too")
    check((target / "already-there" / "new.webp").read_bytes() == b"new",
          "an existing destination is left untouched")
    check((source / "already-there" / "old.webp").exists(),
          "the corresponding source entry is kept when it cannot be moved")
    check("moved=2 skipped=1" in report, f"report counts what happened ({report})")

    report = jmcore.migrate_downloads(source=tmp / "does-not-exist", target=target)
    check(report.startswith("nothing to migrate"),
          f"a missing source is a no-op ({report})")
    report = jmcore.migrate_downloads(source=target, target=target)
    check(report.startswith("nothing to migrate"),
          f"source == target is a no-op ({report})")


def test_entry_point_wiring() -> None:
    print("Android entry point")
    text = ANDROID_MAIN.read_text(encoding="utf-8")
    check("android_storage_probe()" in text,
          "webui/main.py runs the storage probe (main thread, before serving)")
    check(re.search(r"probe\(\)[^\n]*\n[^\n]*default_download_dir\(\)", text) is not None,
          "the probe runs BEFORE the download directory is reported")
    check("migrate_downloads" in text,
          "webui/main.py migrates files written by older versions")


def main() -> int:
    print(f"jmcore under test: {PROJECT_ROOT / 'scripts' / 'jmcore.py'}")
    print(f"os.name={os.name}\n")
    with tempfile.TemporaryDirectory(prefix="jm-android-storage-") as tmp:
        root = Path(tmp)
        test_package_and_fallback(root)
        test_probe_selects_only_writable(root)
        test_posix_guess_is_guarded(root)
        test_migration(root)
    test_entry_point_wiring()

    print()
    if PROBLEMS:
        print("ANDROID STORAGE: FAIL")
        for problem in PROBLEMS:
            print(f"  - {problem}")
        print()
        print("Android 上如果下载目录落在 <ANDROID_PRIVATE> 里，用户在任何文件管理器里都看不到，")
        print("等于白下载。必须优先用应用的外部目录 <external>/Android/data/<package>/files。")
        return 1
    print("ANDROID STORAGE: PASS - 下载目录优先落在用户看得见的外部目录，且只接受可写路径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
