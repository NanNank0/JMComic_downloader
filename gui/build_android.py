#!/usr/bin/env python3
"""
Build (or prepare) the Android APK.

buildozer only runs on Linux/macOS, and `source.dir` in buildozer.spec is `gui/`,
so the shared engine (`scripts/jmcore.py`) has to be staged next to the app before
python-for-android collects the sources. This script performs that staging, runs
buildozer, and always cleans up after itself.

    python gui/build_android.py prepare      # stage the sources and stop
    python gui/build_android.py debug        # stage, build a debug APK, clean up
    python gui/build_android.py release      # stage, build a release APK/AAB, clean up
    python gui/build_android.py logcat       # stream device logs (adb)
    python gui/build_android.py clean        # buildozer clean

On Windows this exits with the instructions for WSL2 / GitHub Actions, because
buildozer has no Windows support at all.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# buildozer imports Kivy's build machinery, and Kivy parses sys.argv on import. This
# script's own switches mean nothing to Kivy, so without this it would print usage
# and exit 2.
os.environ.setdefault("KIVY_NO_ARGS", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# buildozer.spec sets source.dir to this directory, so the shared engine has to be
# staged inside it before python-for-android collects the sources.
SOURCE_DIR = PROJECT_ROOT / "webui"
SPEC = PROJECT_ROOT / "buildozer.spec"

# Files staged into webui/ so the APK contains them, then removed again.
STAGED = ("jmcore.py",)


def stage() -> None:
    for name in STAGED:
        source = PROJECT_ROOT / "scripts" / name
        if not source.is_file():
            raise SystemExit(f"missing {source}")
        shutil.copy2(source, SOURCE_DIR / name)
        print(f"staged  webui/{name}  <- scripts/{name}")


def unstage() -> None:
    for name in STAGED:
        target = SOURCE_DIR / name
        if target.exists():
            target.unlink()
            print(f"removed webui/{name}")


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(
            f"{name} is not installed.\n"
            "  On Debian/Ubuntu/WSL:  pip install buildozer cython\n"
            "  Then install the system deps listed in ANDROID.md"
        )


def windows_bail() -> None:
    print(
        "buildozer does not support Windows builds.\n\n"
        "Use one of:\n"
        "  1. WSL2 (Ubuntu)  --  wsl --install -d Ubuntu\n"
        "     then follow ANDROID.md section 1\n"
        "  2. GitHub Actions --  push a tag, the android workflow builds the APK\n"
        "  3. Any Linux/macOS machine\n",
        file=sys.stderr,
    )


def run(cmd) -> int:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action",
                        choices=["prepare", "unprepare", "debug", "release",
                                 "logcat", "clean", "deploy"],
                        help="What to do.")
    parser.add_argument("--keep", action="store_true",
                        help="Do not remove the staged files afterwards.")
    args = parser.parse_args(argv)

    if not SPEC.is_file():
        raise SystemExit(f"buildozer.spec not found at {SPEC}")

    if args.action == "prepare":
        stage()
        print("\nSources staged. Now run:  buildozer android debug")
        return 0

    if args.action == "unprepare":
        unstage()
        return 0

    if sys.platform == "win32":
        windows_bail()
        return 2

    if args.action == "logcat":
        require_tool("adb")
        return run(["adb", "logcat", "-s", "python:D", "SDL:V"])

    if args.action in ("clean",):
        require_tool("buildozer")
        return run(["buildozer", "android", "clean"])

    require_tool("buildozer")
    stage()
    try:
        code = run(["buildozer", "android", args.action])
    finally:
        if not args.keep:
            unstage()

    if code == 0:
        artifacts = sorted((PROJECT_ROOT / "bin").glob("*.apk")) + \
                    sorted((PROJECT_ROOT / "bin").glob("*.aab"))
        if artifacts:
            print("\nArtifacts:")
            for path in artifacts:
                print(f"  {path.relative_to(PROJECT_ROOT)}  "
                      f"({path.stat().st_size / 1048576:.1f} MB)")
        print("\nInstall on a connected device:  buildozer android deploy run logcat")
    return code


if __name__ == "__main__":
    sys.exit(main())
