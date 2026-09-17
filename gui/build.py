#!/usr/bin/env python3
"""
Package the JMComic downloader for the current platform.

The GUI is `gui/kivy_app.py` (Kivy), shared by every platform. This script wraps
PyInstaller for desktop targets; Android is built with buildozer instead, which
only runs on Linux/macOS.

    python gui/build.py                     # this platform
    python gui/build.py --onedir            # folder instead of one file (faster start)
    python gui/build.py --console           # keep a console for diagnosing a bad build
    python gui/build.py --verify            # build, then run the bundle's --selftest
    python gui/build.py --icon app.ico      # custom icon (--icon app.icns on macOS)
    python gui/build.py --android           # print the Android build instructions

Platform notes
--------------
* Windows  -> dist/jmcomic-downloader.exe
* Linux    -> dist/jmcomic-downloader      (AppImage/.deb live in PACKAGING.md)
* macOS    -> dist/jmcomic-downloader.app  (.dmg needs hdiutil, see PACKAGING.md)

A macOS build must be produced ON macOS: PyInstaller cannot cross-compile, and an
unsigned .app is blocked by Gatekeeper until the user allows it.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# This script imports Kivy to check that it is installed. Kivy parses sys.argv when
# it is imported, and this script's own switches (--console, --onedir, ...) mean
# nothing to Kivy, so it would print its usage and exit with status 2 - which looks
# exactly like a failed build. Set this before ANY kivy import.
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENTRY = PROJECT_ROOT / "gui" / "kivy_app.py"
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
        import kivy  # noqa: F401
    except ImportError:
        raise SystemExit(
            "Kivy is not installed, but the GUI requires it.\n"
            f'Install it with:  "{sys.executable}" -m pip install "kivy[base]" jmcomic'
        )

    missing = []
    for path, why in ((PROJECT_ROOT / "scripts" / "jmcore.py", "shared engine"),
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
       pip install buildozer cython
       buildozer android debug          # -> bin/*.apk

  2. GitHub Actions:  .github/workflows/android.yml  (push a tag, or run manually)

  3. A Linux VM or any Linux machine, same commands as (1).

Configuration lives in buildozer.spec; the curl-cffi workaround lives in
recipes/jmcomic/. Read ANDROID.md before building - it documents the one runtime
invariant the app depends on.
"""


def kivy_deps_args() -> list:
    """
    `--collect-all` flags for whichever Kivy platform dependency packages exist.

    Kivy keeps its SDL2 / GLEW / ANGLE binaries in separate distributions rather than
    inside the kivy package, so a build that only collects `kivy` can fail to start
    with a missing-DLL/so error. The set differs per platform (Windows has angle and
    glew; Linux/macOS have neither), so probe instead of hardcoding.
    """
    import importlib.util

    args = []
    for name in ("kivy_deps.sdl2", "kivy_deps.glew", "kivy_deps.angle",
                 "kivy_deps.sdl2_dev"):
        try:
            # find_spec raises (not returns None) when the PARENT package is absent,
            # e.g. on Linux/macOS where there is no kivy_deps at all.
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            found = False
        if found:
            args += ["--collect-all", name]
    return args


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
        # kivy_app.py imports jmcore from ../scripts; static analysis cannot see it.
        "--paths", str(PROJECT_ROOT / "scripts"),
        "--hidden-import", "jmcore",
        # Do NOT pass --collect-all kivy. PyInstaller already ships hook-kivy.py, which
        # collects Kivy's data files (default config, fonts, glsl shaders). Adding the
        # flag on top of the hook makes PyInstaller walk Kivy's submodules and crash
        # with: ValueError: path must be None or list of paths to look for modules in.
        # Verified: a build without it launches the GUI correctly.
        "--collect-all", "jmcomic",
        # curl_cffi ships native libraries; present on desktop, absent on Android.
        "--collect-all", "curl_cffi",
        # Kivy's platform binaries live in separate `kivy_deps.*` distributions
        # (sdl2, glew, angle). PyInstaller's kivy hook does not always pull them in,
        # so collect whichever are installed.
        *kivy_deps_args(),
        # `common` (the commonX package jmcomic imports) is resolved by following the
        # real imports. Do NOT add --collect-submodules for it: that package exposes
        # no __path__ to pkgutil and PyInstaller raises the same ValueError.
        "--exclude-module", "numpy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "pytest",
        "--exclude-module", "IPython",
        "--exclude-module", "tkinter",
    ]

    if args.icon:
        icon = Path(args.icon).expanduser().resolve()
        if not icon.is_file():
            raise SystemExit(f"icon not found: {icon}")
        cmd += ["--icon", str(icon)]

    return cmd


def expected_target(onedir: bool) -> Path:
    if sys.platform == "darwin":
        return PROJECT_ROOT / "dist" / f"{APP_NAME}.app"
    if sys.platform == "win32":
        if onedir:
            return PROJECT_ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
        return PROJECT_ROOT / "dist" / f"{APP_NAME}.exe"
    if onedir:
        return PROJECT_ROOT / "dist" / APP_NAME / APP_NAME
    return PROJECT_ROOT / "dist" / APP_NAME


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

    if sys.version_info >= (3, 14):
        print("\nWARNING: Kivy has no build for Python 3.14+. Use 3.12 or 3.13.",
              file=sys.stderr)

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

    target = expected_target(args.onedir or sys.platform == "darwin")
    if not target.exists():
        print(f"\nPyInstaller reported success but {project_relative(target)} is missing.",
              file=sys.stderr)
        return 1

    if target.is_dir():
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
        binary = target
        if target.is_dir():
            if sys.platform == "darwin":
                binary = target / "Contents" / "MacOS" / APP_NAME
            else:
                binary = target / APP_NAME
        print(f"\nRunning selftest ({args.verify_id})…")
        check = subprocess.run([str(binary), "--selftest", args.verify_id],
                               cwd=str(PROJECT_ROOT))
        if check.returncode != 0:
            print("Selftest FAILED.", file=sys.stderr)
            return check.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
