#!/usr/bin/env python3
"""
Package the JMComic downloader for the current platform.

The application is `webui/server.py`: a local web UI served on 127.0.0.1, opened in
the user's browser. There is deliberately NO native GUI toolkit, so freezing a build
means PyInstaller has nothing to do beyond Python plus jmcomic.

    python gui/build.py                     # this platform
    python gui/build.py --onedir            # folder instead of one file (faster start)
    python gui/build.py --console           # keep a console (needed to see the URL)
    python gui/build.py --verify            # build, then run the bundle's --selftest
    python gui/build.py --icon app.ico      # custom icon (--icon app.icns on macOS)
    python gui/build.py --android           # print the Android build instructions

Platform notes
--------------
* Windows  -> dist/jmcomic-downloader.exe
* Linux    -> dist/jmcomic-downloader      (AppImage/.deb live in PACKAGING.md)
* macOS    -> dist/jmcomic-downloader.app  (.dmg needs hdiutil, see PACKAGING.md)

A macOS build must be produced ON macOS: PyInstaller cannot cross-compile.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY = PROJECT_ROOT / "webui" / "server.py"
APP_NAME = "jmcomic-downloader"


def project_relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def check_environment() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed.\n"
            f'Install it with:  "{sys.executable}" -m pip install pyinstaller'
        )
    try:
        import jmcomic  # noqa: F401
    except ImportError:
        raise SystemExit(
            "jmcomic is not installed, but the bundle must contain it.\n"
            f'Install it with:  "{sys.executable}" -m pip install jmcomic'
        )

    missing = []
    for path, why in ((PROJECT_ROOT / "webui" / "server.py", "web UI server"),
                      (PROJECT_ROOT / "webui" / "ui.py", "embedded UI"),
                      (PROJECT_ROOT / "scripts" / "jmcore.py", "shared engine"),
                      (PROJECT_ROOT / "scripts" / "jmctl.py", "CLI")):
        if not path.is_file():
            missing.append(f"{project_relative(path)} ({why})")
    if missing:
        raise SystemExit("missing required files: " + ", ".join(missing))


def android_instructions() -> str:
    return """
Android builds do not run through this script.

buildozer + python-for-android require a Linux or macOS host; there is no Windows
support. Use one of:

  1. WSL2 with Ubuntu:
       wsl --install -d Ubuntu
       # inside WSL, from the project directory:
       sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk \\
            python3-pip python3-venv autoconf libtool pkg-config zlib1g-dev \\
            libncurses-dev cmake libffi-dev libssl-dev
       python3 -m venv .venv && . .venv/bin/activate
       pip install buildozer cython==0.29.36
       python gui/build_android.py debug     # -> bin/*.apk

  2. GitHub Actions:  .github/workflows/android.yml  (push a tag, or run manually)

  3. A Linux VM or any Linux machine, same commands as (1).

Configuration lives in buildozer.spec; the curl-cffi workaround lives in
recipes/jmcomic/. Read ANDROID.md before building - it documents the one runtime
invariant the app depends on.
"""


def build_args(args) -> list:
    onedir = args.onedir
    if sys.platform == "darwin" and not args.onefile:
        # A .app bundle is inherently a directory; --onefile only wraps the inner
        # binary and makes Gatekeeper handling worse.
        onedir = True
    mode = "--onedir" if onedir else "--onefile"
    windowed = "--console" if args.console else "--windowed"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(ENTRY),
        "--name", APP_NAME,
        mode,
        windowed,
        "--noconfirm",
        "--clean",
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(PROJECT_ROOT / "build"),
        "--specpath", str(PROJECT_ROOT / "build"),
        # server.py imports jmcore (../scripts) and ui (same dir); ui is a normal
        # import so only the sibling scripts dir needs adding to the search path.
        "--paths", str(PROJECT_ROOT / "scripts"),
        "--hidden-import", "jmcore",
        "--hidden-import", "ui",
        "--collect-all", "jmcomic",
        # curl_cffi ships native libraries; present on desktop, absent on Android.
        "--collect-all", "curl_cffi",
        # `common` (the commonX package jmcomic imports) is resolved by following the
        # real imports. Do NOT add --collect-submodules for it: that package exposes
        # no __path__ to pkgutil and PyInstaller raises
        # "path must be None or list of paths to look for modules in".
        "--exclude-module", "numpy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
        # Stdlib, but PyInstaller can drag tcl/tk in through a dependency; excluding it
        # keeps the bundle noticeably smaller.
        "--exclude-module", "tkinter",
    ]

    if args.icon:
        icon = Path(args.icon).expanduser().resolve()
        if not icon.is_file():
            raise SystemExit(f"icon not found: {icon}")
        cmd += ["--icon", str(icon)]

    return cmd


def expected_target(onedir: bool, windowed: bool) -> Path:
    """
    Where PyInstaller will put the artifact.

    macOS only produces a `.app` bundle when `--windowed` is used; with `--console` it
    produces a plain directory/binary instead. Getting this wrong makes a successful
    build look like a failure ("reported success but ... is missing").
    """
    if sys.platform == "darwin" and windowed:
        return PROJECT_ROOT / "dist" / f"{APP_NAME}.app"
    if sys.platform == "win32":
        if onedir:
            return PROJECT_ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
        return PROJECT_ROOT / "dist" / f"{APP_NAME}.exe"
    if onedir:
        return PROJECT_ROOT / "dist" / APP_NAME / APP_NAME
    return PROJECT_ROOT / "dist" / APP_NAME


def frozen_binary(target: Path) -> Path:
    """The executable inside a bundle, for running --selftest."""
    if target.suffix == ".app":
        return target / "Contents" / "MacOS" / APP_NAME
    if target.is_dir():
        return target / APP_NAME
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onedir", action="store_true",
                        help="Build a folder bundle instead of a single file.")
    parser.add_argument("--onefile", action="store_true",
                        help="Force a single file even on macOS.")
    parser.add_argument("--console", action="store_true",
                        help="Keep a console window; useful for diagnosing a bad build.")
    parser.add_argument("--clean", action="store_true",
                        help="Delete build/ and dist/ before building.")
    parser.add_argument("--icon", default=None,
                        help="Path to .ico (Windows) or .icns (macOS).")
    parser.add_argument("--verify", action="store_true",
                        help="After building, run the bundle's --selftest.")
    parser.add_argument("--verify-id", default="438696",
                        help="Album id used by --verify.")
    parser.add_argument("--android", action="store_true",
                        help="Print Android build instructions and exit.")
    args = parser.parse_args(argv)

    if args.android:
        print(android_instructions())
        return 0

    print(f"platform: {sys.platform} ({platform.machine()})")
    print(f"python:   {sys.executable} ({platform.python_version()})")

    check_environment()

    if args.clean:
        for directory in (PROJECT_ROOT / "build", PROJECT_ROOT / "dist"):
            if directory.exists():
                shutil.rmtree(directory)
                print(f"removed {project_relative(directory)}")

    cmd = build_args(args)
    print("\nrunning:\n  " + " ".join(cmd) + "\n")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("\nBuild failed. Re-run with --console to see the runtime error.",
              file=sys.stderr)
        return result.returncode

    windowed = not args.console
    target = expected_target(args.onedir or (sys.platform == "darwin" and windowed),
                             windowed)
    if not target.exists():
        print(f"\nPyInstaller reported success but {project_relative(target)} is missing.",
              file=sys.stderr)
        return 1

    if target.is_dir() or target.suffix == ".app":
        print(f"\nBuilt: {project_relative(target)} (bundle)")
    else:
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"\nBuilt: {project_relative(target)}  ({size_mb:.1f} MB)")

    if sys.platform == "darwin":
        print("\nmacOS: the .app is unsigned, so Gatekeeper will block it on other\n"
              "machines. See PACKAGING.md for signing/notarization and the .dmg step.")
    elif sys.platform.startswith("linux"):
        print("\nLinux: see PACKAGING.md for AppImage / .deb packaging.")

    if args.verify:
        binary = frozen_binary(target)
        print(f"\nRunning selftest ({args.verify_id})…")
        check = subprocess.run([str(binary), "--selftest", args.verify_id],
                               cwd=str(PROJECT_ROOT))
        if check.returncode != 0:
            print("Selftest FAILED.", file=sys.stderr)
            return check.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
