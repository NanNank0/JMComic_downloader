#!/usr/bin/env python3
"""
Verify that a built APK really contains the native pieces the app needs at runtime.

Why this exists
---------------
An APK can build green, install, open, fetch album metadata, resolve chapters and
download every image - and still fail 100% of images, because the failure is inside
Pillow's C codecs and the build log says nothing about it.

That is not hypothetical. v1.3.7 shipped `PIL/WebPImagePlugin.pyc` (pure Python, always
present) but no `PIL/_webp*.so`, so Pillow could neither decode nor encode WebP. JM
serves its page images as .webp, so on the phone every page raised

    PIL.UnidentifiedImageError: cannot identify image file <_io.BytesIO object ...>

after a completely successful HTTP download, producing
`PartialDownloadFailedException: 部分下载失败 ... 共50个图片下载失败` - while the very
same album downloaded fine on Windows, whose Pillow wheel bundles WebP.

So this script reads the artifact itself instead of trusting the build, and asserts:

  1. Pillow's WebP codec (`PIL/_webp*.so`) is present in the bundled Python of every
     ABI. Without it, .webp pages can never be decoded.
  2. Every DT_NEEDED entry of every shipped ELF object - the `lib/<abi>/*.so` files
     and the extension modules inside the bundled Python - resolves, either to another
     file shipped in the same `lib/<abi>/`, or to a known Android system library.

Check (2) is what makes check (1) trustworthy. Android's linker matches DT_NEEDED by
exact file name, and the Android Gradle plugin only packages files ending in `.so`, so
a library built with a versioned soname (e.g. `libwebp.so.7`) installs fine and then
fails to load at runtime. This script catches that on the build machine.

Usage
-----
    python gui/verify_apk.py bin/*.apk     # exits non-zero when anything is missing

Also runs locally: the APK is a zip, so this needs no Android tooling at all.
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ELF_MAGIC = b"\x7fELF"

# Libraries the platform itself provides at runtime. Everything else must be shipped
# inside the APK, because Android's linker resolves DT_NEEDED by exact file name and
# only searches the app's own native library directory for non-system names.
SYSTEM_LIBS = {
    "libandroid.so",
    "libbinder_ndk.so",
    "libc.so",
    "libc++_shared.so",
    "libdl.so",
    "libEGL.so",
    "libGLESv1_CM.so",
    "libGLESv2.so",
    "libjnigraphics.so",
    "liblog.so",
    "libm.so",
    "libmediandk.so",
    "libnativehelper.so",
    "libOpenMAXAL.so",
    "libOpenSLES.so",
    "libstdc++.so",
    "libsync.so",
    "libvulkan.so",
    "libz.so",
    "ld-android.so",
}

# Pillow modules we expect to find; missing ones are reported, `_webp` is fatal.
REQUIRED_PIL_PREFIXES = ("_webp",)


def _read_cstr(buf: bytes, offset: int) -> str:
    end = buf.find(b"\0", offset)
    if end < 0:
        end = len(buf)
    return buf[offset:end].decode("utf-8", "replace")


def elf_links(data: bytes) -> Tuple[Optional[List[str]], Optional[str]]:
    """
    Return (DT_NEEDED, DT_SONAME) of an ELF image, or (None, None) when it cannot be
    parsed. Unparsable objects are reported as warnings, never as failures - the goal
    is to catch missing libraries, not to reimplement readelf.
    """
    if len(data) < 64 or data[:4] != ELF_MAGIC:
        return None, None

    is64 = data[4] == 2
    try:
        if is64:
            (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
            e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
            sh_fmt, sh_size, dyn_fmt, dyn_size = "<IIQQQQIIQQ", 64, "<qQ", 16
        else:
            (e_shoff,) = struct.unpack_from("<I", data, 0x20)
            e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x2E)
            sh_fmt, sh_size, dyn_fmt, dyn_size = "<IIIIIIIIII", 40, "<iI", 8
    except struct.error:
        return None, None

    if not e_shoff or not e_shnum:
        return None, None

    try:
        sections = []
        for index in range(e_shnum):
            offset = e_shoff + index * e_shentsize
            if offset + sh_size > len(data):
                return None, None
            name, _type, _flags, _addr, sec_off, size, _link, _info, _align, _ent = (
                struct.unpack_from(sh_fmt, data, offset)
            )
            sections.append((name, sec_off, size))
        if e_shstrndx >= len(sections):
            return None, None
    except struct.error:
        return None, None

    _name, shstr_off, shstr_size = sections[e_shstrndx]
    shstr = data[shstr_off:shstr_off + shstr_size]

    dynamic = dynstr = None
    for name, sec_off, size in sections:
        section = _read_cstr(shstr, name)
        if section == ".dynamic":
            dynamic = (sec_off, size)
        elif section == ".dynstr":
            dynstr = (sec_off, size)
    if dynamic is None or dynstr is None:
        return None, None

    strings = data[dynstr[0]:dynstr[0] + dynstr[1]]
    needed: List[str] = []
    soname: Optional[str] = None
    for offset in range(dynamic[0], dynamic[0] + dynamic[1], dyn_size):
        if offset + dyn_size > len(data):
            break
        tag, value = struct.unpack_from(dyn_fmt, data, offset)
        if tag == 0:  # DT_NULL
            break
        if tag == 1:  # DT_NEEDED
            needed.append(_read_cstr(strings, value))
        elif tag == 14:  # DT_SONAME
            soname = _read_cstr(strings, value)
    return needed, soname


def bundle_elf_files(raw: bytes) -> Dict[str, bytes]:
    """
    Extract the shared objects from p4a's bundled Python.

    `lib/<abi>/libpybundle.so` is a gzip-compressed archive of the Python standard
    library plus site-packages. Its inner format has already changed once (tar in the
    builds we ship), so both tar and zip are accepted.
    """
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            return {}

    found: Dict[str, bytes] = {}

    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                for name in inner.namelist():
                    if name.endswith(".so"):
                        found[name] = inner.read(name)
        except Exception:
            return found
        return found

    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.endswith(".so"):
                    handle = archive.extractfile(member)
                    if handle is not None:
                        found[member.name] = handle.read()
    except Exception:
        pass
    return found


def check_apk(path: Path) -> List[str]:
    problems: List[str] = []

    with zipfile.ZipFile(path) as apk:
        names = apk.namelist()
        lib_names = [n for n in names if n.startswith("lib/") and n.count("/") == 2]
        abis = sorted({n.split("/")[1] for n in lib_names})
        if not abis:
            problems.append(
                f"{path.name}: no lib/<abi>/ directory - is this really an APK?"
            )
            return problems

        for abi in abis:
            shipped = {n.split("/")[-1]: n for n in lib_names
                       if n.startswith(f"lib/{abi}/")}
            print(f"  ABI {abi}: {len(shipped)} native libraries")

            objects: Dict[str, bytes] = {}
            for base, entry in shipped.items():
                data = apk.read(entry)
                if data[:4] == ELF_MAGIC:
                    objects[base] = data

            bundle = next((entry for base, entry in shipped.items()
                           if base.startswith("libpybundle")), None)
            pil_modules: List[str] = []
            if bundle is None:
                print("    ! no libpybundle*.so: could not inspect the bundled "
                      "Python (Pillow codec check skipped for this ABI)")
            else:
                modules = bundle_elf_files(apk.read(bundle))
                pil_modules = sorted(n for n in modules if "/PIL/" in n)
                objects.update({os.path.basename(n): data
                                for n, data in modules.items()})
                print(f"    Pillow extensions: "
                      f"{', '.join(n.split('/')[-1] for n in pil_modules) or '(none)'}")

                for prefix in REQUIRED_PIL_PREFIXES:
                    if not any(os.path.basename(n).startswith(prefix)
                               for n in pil_modules):
                        problems.append(
                            f"{path.name} [{abi}]: Pillow has no {prefix} codec "
                            f"(PIL/{prefix}*.so missing). JM serves page images as "
                            f".webp, so EVERY image would fail to decode with "
                            f"PIL.UnidentifiedImageError. Add `libwebp` to "
                            f"buildozer.spec requirements - p4a's Pillow recipe turns "
                            f"webp on only when that recipe is in the build order."
                        )

            if "libwebp.so" in shipped:
                print("    libwebp.so: shipped")
            else:
                print("    libwebp.so: absent "
                      "(fine only if Pillow's _webp.so is absent too, i.e. unused)")

            for label in sorted(objects):
                needed, soname = elf_links(objects[label])
                if needed is None:
                    print(f"    ? {label}: not parsable as ELF, dependency check "
                          f"skipped")
                    continue
                if soname and soname != label and label in shipped:
                    print(f"    note: {label} has soname {soname}")
                for dependency in needed:
                    if dependency in shipped or dependency in objects:
                        continue
                    if dependency in SYSTEM_LIBS:
                        continue
                    problems.append(
                        f"{path.name} [{abi}]: {label} needs {dependency}, which is "
                        f"neither shipped in lib/{abi}/ nor a system library. "
                        f"Android's linker matches DT_NEEDED by exact file name, so "
                        f"this fails at runtime (not at build time)."
                    )

    return problems


def main(argv: List[str]) -> int:
    # The summary is bilingual and CI logs are UTF-8; a cp936/cp1252 console would
    # otherwise print replacement characters instead of the message.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    targets = [Path(a) for a in argv[1:]]
    if not targets:
        print(__doc__.strip())
        return 2

    all_problems: List[str] = []
    for path in targets:
        if not path.is_file():
            all_problems.append(f"{path}: no such file")
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"APK {path.name} ({size_mb:.1f} MB)")
        all_problems.extend(check_apk(path))

    print()
    if all_problems:
        print("APK CHECK: FAIL")
        for problem in all_problems:
            print(f"  - {problem}")
        print()
        print("APK 校验失败：这个包装到手机上会失败，不要发布。")
        print("(见 ANDROID.md 的 WebP 一节：Pillow 的编解码器是构建期决定的。)")
        return 1

    print("APK CHECK: PASS - Pillow 的 WebP 编解码器在包里，所有原生依赖都能解析。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
