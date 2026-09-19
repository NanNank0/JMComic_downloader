#!/usr/bin/env python3
"""
jmcore - the platform-independent JMComic download engine.

Three front ends are thin layers over this module: `jmctl.py` (the JSON CLI),
`webui/` (the same local web UI on every platform, including the Android WebView) and
the packaged desktop/Android bundles. Nothing here needs a GUI toolkit or a terminal,
which is what makes both the frozen desktop builds and the Android build possible.

Android-specific note
---------------------
`jmcomic` declares `curl-cffi` as a hard dependency, but python-for-android has no
recipe for it. curl-cffi exists to impersonate a browser's TLS fingerprint, and the
JM endpoints do not require it - verified by downloading a full chapter through the
pure-Python `requests` backend. So on Android we select that backend and let the
packaging step install jmcomic with `--no-deps`. `curl_cffi` is imported lazily by
commonX, so as long as the requests backend is used it is never touched.

Two Android traps documented at their implementations below, because both cost a real
debugging round: `ensure_ctypes_util_importable()` (main-thread JNI warm-up) and
`android_storage_probe()` (the app-private download directory is invisible to users).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA_VERSION = 1

# Export suffix -> (Feature attribute name, produced file extension)
EXPORT_KINDS: Dict[str, tuple] = {
    "pdf": ("export_pdf", "pdf"),
    "zip": ("export_zip", "zip"),
    "png": ("export_long_img", "png"),
}

# HTTP backend selection. curl_cffi is the upstream default but is native code and
# unavailable on Android; `requests` is pure Python and verified to work.
DEFAULT_HTTP_BACKEND = "curl_cffi"
ANDROID_HTTP_BACKEND = "requests"

# Optional dependencies per export format, used to warn before a download rather
# than silently producing nothing.
#
# `pdf` deliberately depends on PIL (Pillow) rather than img2pdf: img2pdf hard-depends
# on pikepdf, a QPDF binding with no Android wheel and no python-for-android recipe, so
# it simply cannot be installed on Android. Pillow is required anyway and can write
# multi-page PDFs itself, so `_export_pdf_with_pillow` produces the same kind of
# artifact everywhere. See ANDROID.md.
EXPORT_DEPENDENCIES: Dict[str, tuple] = {
    "pdf": ("PIL",),
    "zip": (),
    "png": ("PIL",),
}


class OperationError(Exception):
    """A failure that should be surfaced to the user, not as a traceback."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------- #
# environment
# --------------------------------------------------------------------------- #

def android_signals() -> Dict[str, Any]:
    """
    Cheap, JNI-free evidence for whether we are running inside an Android app.

    Deliberately does NOT call `platform.platform()`. On Android that reaches
    CPython's `platform.android_ver()`, which imports pyjnius and resolves classes
    through the app's ClassLoader - and pyjnius looks that up via
    `org.kivy.android.PythonActivity`. When the activity is not ready yet, the lookup
    falls back to the system loader and blows up with:

        JavaException: java.lang.ClassNotFoundException:
        Didn't find class "org.kivy.android.PythonActivity"
        on path: DexPathList[[directory "."], ...]

    (that `directory "."` is the system loader, not the app's). That crash was
    observed on a real device. The markers below cannot trigger JNI at all.
    """
    return {
        "sys.platform": sys.platform,
        "has_getandroidapilevel": hasattr(sys, "getandroidapilevel"),
        "ANDROID_ARGUMENT": bool(os.environ.get("ANDROID_ARGUMENT")),
        "ANDROID_PRIVATE": bool(os.environ.get("ANDROID_PRIVATE")),
        "ANDROID_APP_PATH": bool(os.environ.get("ANDROID_APP_PATH")),
        "P4A_MINSDK": bool(os.environ.get("P4A_MINSDK")),
        "P4A_IS_WINDOWED": bool(os.environ.get("P4A_IS_WINDOWED")),
    }


def is_android() -> bool:
    """
    True when running inside a python-for-android app.

    Signals, in order of reliability:

    * `sys.getandroidapilevel` - a builtin CPython only defines on Android builds;
    * `sys.platform == 'android'`;
    * `ANDROID_ARGUMENT` / `ANDROID_PRIVATE` / `ANDROID_APP_PATH` - set by p4a's
      bootstrap. NOTE: the `webview` bootstrap does NOT set these (the qt/sdl2/sdl3/
      service_only ones do), which is why they cannot be the only signal here;
    * `P4A_MINSDK` / `P4A_IS_WINDOWED` - written into the APK's `p4a_env_vars.txt`.

    Never probes the platform module; see `android_signals` for why.
    """
    if hasattr(sys, "getandroidapilevel"):
        return True
    if sys.platform == "android":
        return True
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE") \
            or os.environ.get("ANDROID_APP_PATH"):
        return True
    if os.environ.get("P4A_MINSDK") or os.environ.get("P4A_IS_WINDOWED"):
        return True
    return False


def default_http_backend() -> str:
    return ANDROID_HTTP_BACKEND if is_android() else DEFAULT_HTTP_BACKEND


# Set to True when load_jmcomic() had to stub curl_cffi out (see below).
CURL_CFFI_STUBBED = False


def _install_curl_cffi_stub() -> bool:
    """
    Make `import jmcomic` work on platforms where curl_cffi cannot exist.

    jmcomic 2.7.7 imports it at MODULE scope:

        jmcomic/jm_async_client.py:  from curl_cffi.requests import AsyncSession

    and `jmcomic/__init__.py` eagerly imports that module, so a plain `import jmcomic`
    fails with `No module named 'curl_cffi'` when it is absent. curl_cffi is a Rust +
    CFFI extension and python-for-android has no recipe for it, so on Android it is
    genuinely unavailable.

    That import only needs `AsyncSession`, whose only consumer is the ASYNC client -
    which this application never uses: it drives the synchronous API through the
    `requests` HTTP backend. A stub module is therefore registered in sys.modules to
    satisfy the import. It raises loudly if it is ever actually used, rather than
    silently doing the wrong thing.

    Returns True when a stub was installed.
    """
    global CURL_CFFI_STUBBED

    import types

    if "curl_cffi" in sys.modules:
        return False
    try:
        import curl_cffi  # noqa: F401
        return False
    except Exception:
        pass

    class _AsyncSessionUnavailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "curl_cffi is not available on this platform (python-for-android "
                "cannot build it). This build uses the synchronous jmcomic API with "
                "the 'requests' HTTP backend; the async client is unsupported here."
            )

    package = types.ModuleType("curl_cffi")
    requests_module = types.ModuleType("curl_cffi.requests")
    requests_module.AsyncSession = _AsyncSessionUnavailable
    requests_module.Session = _AsyncSessionUnavailable
    package.requests = requests_module
    sys.modules["curl_cffi"] = package
    sys.modules["curl_cffi.requests"] = requests_module

    CURL_CFFI_STUBBED = True
    return True


def ensure_ctypes_util_importable() -> str:
    """
    Make `import ctypes.util` survivable, preferring to do it on the MAIN thread.

    Why this is needed
    ------------------
    python-for-android patches CPython's `Lib/ctypes/util.py` so that it *starts* with:

        if True:
            from android._ctypes_library_finder import find_library as _find_lib
            def find_library(name):
                return _find_lib(name)
        elif os.name == "nt":
            ...

    (see p4a's `recipes/python3/patches/cpython-311-ctypes-find-library.patch`). So on
    Android ANY `import ctypes.util` imports the `android` Cython module, which goes
    through jnius and needs the Activity's ClassLoader. From a plain Python worker
    thread JNI resolves classes with the *system* loader, so it dies with:

        JavaException: ClassNotFoundException:
        Didn't find class "org.kivy.android.PythonActivity"
        on path: DexPathList[[directory "."], ...]

    And PyCryptodome imports `ctypes.util` whenever its CFFI backend is unusable - which
    on p4a it is ("CFFI with optimize=2 fails due to pycparser bug") - while jmcomic
    needs PyCryptodome (AES) to decode API responses. So the download path reaches that
    import, and our downloads run on a worker thread.

    Remedy, in order
    ----------------
    1. Import it now. Called from the main thread this succeeds, and because Python
       caches modules the worker thread's later import is a no-op.
    2. If even that fails, install a minimal `android._ctypes_library_finder`
       replacement that searches the platform lib directories without JNI (the same
       logic stock CPython uses on Android). This is logged loudly because it is a
       workaround, not a fix.

    Returns a human-readable description for logging. Safe to call anywhere and more
    than once; a no-op off Android.
    """
    if not is_android():
        return "not android; ctypes.util left alone"

    import importlib

    if "ctypes.util" in sys.modules:
        return "ctypes.util already imported"

    try:
        importlib.import_module("ctypes.util")
        return "ctypes.util imported on the calling thread"
    except Exception as first_error:
        reason = f"{type(first_error).__name__}: {first_error}"

    # Fall back to a JNI-free find_library.
    try:
        _install_android_ctypes_shim()
    except Exception as second_error:
        return (f"FAILED to make ctypes.util importable "
                f"(direct: {reason}; with shim: {type(second_error).__name__}: "
                f"{second_error})")

    return ("used a JNI-free ctypes.util shim because the real one failed on this "
            f"thread ({reason})")


def _install_android_ctypes_shim() -> None:
    """
    Replace `android._ctypes_library_finder` with a JNI-free implementation.

    Only used when the real import failed. Provides the same `find_library` contract
    that p4a's patched `ctypes/util.py` expects, using stock CPython's Android search
    logic, and raises if `ctypes.util` still cannot be imported.
    """
    import importlib
    import types

    for name in ("android._ctypes_library_finder", "android"):
        sys.modules.pop(name, None)

    def _find_library_no_jni(name):
        directory = "/system/lib"
        try:
            if "64" in os.uname().machine:
                directory += "64"
        except Exception:
            pass
        candidate = f"{directory}/lib{name}.so"
        return candidate if os.path.exists(candidate) else None

    package = types.ModuleType("android")
    package.__path__ = []          # mark as a package so submodule import works
    submodule = types.ModuleType("android._ctypes_library_finder")
    submodule.find_library = _find_library_no_jni
    package._ctypes_library_finder = submodule
    sys.modules["android"] = package
    sys.modules["android._ctypes_library_finder"] = submodule

    importlib.import_module("ctypes.util")


def load_jmcomic():
    """Import jmcomic, or raise a handled error explaining how to install it."""
    stubbed = _install_curl_cffi_stub()
    try:
        import jmcomic
    except ImportError as e:
        raise OperationError(
            f"jmcomic is not importable: {e}",
            hint=(
                f'Install it into the interpreter you are running: '
                f'"{sys.executable}" -m pip install jmcomic'
            ),
        ) from e
    if stubbed:
        # Surface it: on Android this only reaches logcat, which is exactly where a
        # future "async client unsupported" report would need explaining.
        try:
            print("[jmcore] curl_cffi unavailable; installed a stub so jmcomic imports "
                  "(async client disabled, sync API + requests backend in use)")
        except Exception:
            pass
    return jmcomic


def route_logs_to_stderr() -> None:
    """
    Send jmcomic's log records to stderr instead of stdout.

    jmcomic installs its console handler on sys.stdout, which corrupts any
    machine-readable output on stdout (the CLI contract) and is undesirable in a
    GUI. Existing handlers are replaced.
    """
    handler = logging.StreamHandler(sys.stderr)
    try:
        # Put the library's CJK log text through a UTF-8 stream so a legacy console
        # codec (cp936) cannot raise or mangle it.
        handler.setStream(
            open(sys.stderr.fileno(), "w", encoding="utf-8", errors="replace",
                 buffering=1, closefd=False)
        )
    except Exception:
        pass
    _install_handler(handler)


def attach_log_sink(sink) -> logging.Handler:
    """
    Forward jmcomic log lines to `sink(text)`. Returns the handler so the caller
    can detach it (a GUI must not leak one per run).
    """
    handler = logging.Handler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    def emit(record):
        try:
            sink(handler.format(record))
        except Exception:
            pass

    handler.emit = emit
    _install_handler(handler, replace=False)
    return handler


def detach_log_sink(handler: logging.Handler) -> None:
    logging.getLogger("jmcomic").removeHandler(handler)


def _install_handler(handler: logging.Handler, replace: bool = True) -> None:
    logger = logging.getLogger("jmcomic")
    if replace:
        logger.handlers = [handler]
    elif handler not in logger.handlers:
        logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    # jmcomic adds a stdout handler lazily on first log call; block that path.
    logger.propagate = False


def reconfigure_streams_utf8() -> None:
    """Make stdout/stderr tolerate CJK on consoles with a legacy default codec."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# ids and option construction
# --------------------------------------------------------------------------- #

def require_id(jmcomic, value: str) -> str:
    """Parse 'JM123', 'https://18comic.vip/album/123', '123' -> '123'."""
    try:
        return jmcomic.JmcomicText.parse_to_jm_id(value)
    except Exception as e:
        raise OperationError(
            f"could not parse a JM id out of {value!r}: {e}",
            hint="Pass a bare number, a 'JM<number>' string, or a JM URL.",
        ) from e


def split_id_text(text: str) -> List[str]:
    """Split user-typed free text into candidate id tokens."""
    return [t for t in re.split(r"[\s,;，、]+", text or "") if t]


def apply_http_backend(option, backend: Optional[str]) -> Optional[str]:
    """
    Point the option's postman at the requested HTTP backend.

    Returns the backend actually set, or None when the request could not be
    applied (in which case the option keeps jmcomic's default).
    """
    if not backend:
        return None
    try:
        option.client.postman.type = backend
        # `impersonate` is a curl_cffi concept; harmless for requests, but drop it
        # so a pure-Python backend is not handed an argument it may reject.
        if backend.startswith("requests"):
            meta = getattr(option.client.postman, "meta_data", None)
            if meta is not None and hasattr(meta, "impersonate"):
                meta.impersonate = None
        return backend
    except Exception:
        return None


def build_option(jmcomic, option_file: Optional[str] = None,
                 client_impl: Optional[str] = None,
                 http_backend: Optional[str] = None):
    """Create a JmOption from a config file (if any), then apply overrides."""
    if option_file:
        path = Path(option_file).expanduser()
        if not path.is_file():
            raise OperationError(
                f"option file not found: {path}",
                hint="Run the `config` command to generate a starter option.yml.",
            )
        try:
            option = jmcomic.create_option_by_file(str(path))
        except Exception as e:
            raise OperationError(f"failed to load option file {path}: {e}") from e
    else:
        option = jmcomic.JmOption.default()

    if client_impl:
        option.client.impl = client_impl
    apply_http_backend(option, http_backend)
    return option


def new_client(jmcomic, option):
    try:
        return option.new_jm_client()
    except Exception as e:
        raise OperationError(
            f"could not create a JM client: {e}",
            hint=(
                "Network access to the JM domain is required. If you are behind a "
                "proxy, set client.postman.meta_data.proxies in option.yml, or switch "
                "client.impl between 'html' and 'api'."
            ),
        ) from e


def default_download_dir() -> Path:
    """
    A sensible per-platform default download location.

    On Android this prefers the app's EXTERNAL files directory
    (`<external>/Android/data/<package>/files/downloads`), which needs no permission
    and is browsable from a file manager and over USB, over the app-private
    `/data/user/0/<package>/files/downloads`, which no file manager can ever show.
    See `android_storage_probe()`, which must run first and on the main thread.
    """
    if is_android():
        if _ANDROID_EXTERNAL_ROOT is not None:
            return _ANDROID_EXTERNAL_ROOT / "downloads"
        return _android_private_root() / "downloads"
    return Path.home() / "Downloads" / "JMComic"


# --------------------------------------------------------------------------- #
# Android storage: downloads must land somewhere the user can actually find
# --------------------------------------------------------------------------- #

# Set once by android_storage_probe() (main thread only) to the app's EXTERNAL files
# directory. Everything under `<ANDROID_PRIVATE>` - the default p4a gives us - is
# unreadable for every file manager, for USB/MTP and for `adb pull` without run-as, so
# a download that "succeeds" there is effectively lost from the user's point of view.
_ANDROID_EXTERNAL_ROOT: Optional[Path] = None


def android_external_root() -> Optional[Path]:
    """The external root chosen by android_storage_probe(), or None."""
    return _ANDROID_EXTERNAL_ROOT


def _android_private_root() -> Path:
    """The app's private, non-browsable root (p4a's app files directory)."""
    for key in ("ANDROID_PRIVATE", "ANDROID_APP_PATH"):
        base = os.environ.get(key)
        if base:
            return Path(base)
    # server.py lives in the app dir (<files>/app), whose parent is writable.
    try:
        return Path(__file__).resolve().parent.parent
    except Exception:
        return Path(os.getcwd())


def _android_package_name() -> Optional[str]:
    """
    Dig the app's package name out of p4a's environment variables.

    `/data/user/0/io.github.nannank0.jmcomicdownloader/files` -> the package name, which
    is what the external files path is built from when jnius is not usable.
    """
    for key in ("ANDROID_PRIVATE", "ANDROID_APP_PATH", "ANDROID_ARGUMENT"):
        value = os.environ.get(key) or ""
        match = re.search(r"/data/(?:user/\d+|data)/([^/]+)", value)
        if match:
            return match.group(1)
    return None


def _jnius_external_files_dir() -> Optional[str]:
    """
    `Context.getExternalFilesDir(null)` - the authoritative answer, but jnius and
    therefore MAIN-THREAD ONLY: `org.kivy.android.PythonActivity` is an application
    class, which only the Activity's ClassLoader can resolve. Called from a worker
    thread, JNI's FindClass falls back to the system ClassLoader and raises
    `ClassNotFoundException` (the same trap documented in
    ensure_ctypes_util_importable()).
    """
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        if activity is None:
            return None
        directory = activity.getExternalFilesDir(None)
        return str(directory.getAbsolutePath()) if directory is not None else None
    except Exception:
        return None


def _writable_directory(path: Path) -> bool:
    """True only when we can really create the directory and write inside it."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".jmcomic-write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except Exception:
        return False


def _candidate_external_roots() -> List[tuple]:
    """
    External roots to try, best first, as (path, how-we-found-it) pairs.
    """
    candidates: List[tuple] = []

    jnius_path = _jnius_external_files_dir()
    if jnius_path:
        candidates.append((Path(jnius_path), "Activity.getExternalFilesDir()"))

    # Guessing is only meaningful on a real device: on Windows `Path("/sdcard/x")`
    # silently means `C:\sdcard\x`, so that branch is deliberately POSIX-only.
    if os.name == "posix":
        package = _android_package_name()
        if package:
            for base in (os.environ.get("EXTERNAL_STORAGE"), "/sdcard",
                         "/storage/emulated/0"):
                if base:
                    candidates.append(
                        (Path(base) / "Android" / "data" / package / "files",
                         f"{base}/Android/data/{package}/files"))

    unique: List[tuple] = []
    seen = set()
    for root, how in candidates:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append((root, how))
    return unique


def android_storage_probe() -> str:
    """
    Pick a download root the user can actually reach, and report what happened.

    Called once from the Android entry point, on the MAIN thread. Every candidate is
    write-tested before it is trusted; falling back to the private directory keeps the
    app working even when nothing else is writable.
    """
    global _ANDROID_EXTERNAL_ROOT

    if not is_android():
        return "not android"
    if _ANDROID_EXTERNAL_ROOT is not None:
        return f"already set: {_ANDROID_EXTERNAL_ROOT / 'downloads'}"

    rejected = []
    for root, how in _candidate_external_roots():
        if _writable_directory(root / "downloads"):
            _ANDROID_EXTERNAL_ROOT = root
            return f"{root / 'downloads'} (via {how}) - browsable"
        rejected.append(f"{root} ({how}) not writable")

    detail = ("; ".join(rejected[:3]) + "; ") if rejected else ""
    return (f"{detail}keeping app-private {_android_private_root() / 'downloads'}, "
            f"which NO file manager can show")


def migrate_downloads(source: Optional[Path] = None,
                      target: Optional[Path] = None) -> str:
    """
    Move already-downloaded albums from the invisible private directory into the
    browsable one, so upgrading does not look like the files were lost.

    Pure file operations (no jnius), so it is safe to run off the main thread. Anything
    already present at the destination is left untouched. Returns a log-ready report.
    """
    source = source if source is not None else _android_private_root() / "downloads"
    target = target if target is not None else default_download_dir()

    if source == target or not source.is_dir():
        return f"nothing to migrate ({source})"

    moved = skipped = failed = 0
    try:
        entries = sorted(source.iterdir())
    except Exception as exc:
        return f"cannot list {source}: {exc}"

    for entry in entries:
        destination = target / entry.name
        if destination.exists():
            skipped += 1
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(destination))
            moved += 1
        except Exception:
            failed += 1
    return f"{source} -> {target}: moved={moved} skipped={skipped} failed={failed}"


# --------------------------------------------------------------------------- #
# entity -> plain dict
# --------------------------------------------------------------------------- #

def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def base_entity_fields(entity) -> Dict[str, Any]:
    return {
        "durationSec": round(getattr(entity, "duration", 0.0) or 0.0, 3),
        "savePath": str(getattr(entity, "save_path", "") or ""),
    }


def album_to_dict(album) -> Dict[str, Any]:
    episodes = []
    for item in (getattr(album, "episode_list", None) or []):
        # (photo_id, photo_index, photo_title, photo_pub_date)
        episodes.append(
            {
                "photoId": str(item[0]),
                "index": _int_or_none(item[1]),
                "title": item[2],
                "pubDate": item[3] if len(item) > 3 else None,
            }
        )

    data = {
        "kind": "album",
        "id": str(album.album_id),
        "title": album.name,
        "authors": list(album.authors or []),
        "tags": list(album.tags or []),
        "works": list(album.works or []),
        "actors": list(album.actors or []),
        "pageCount": _int_or_none(album.page_count),
        "pubDate": album.pub_date,
        "updateDate": album.update_date,
        "likes": album.likes,
        "views": album.views,
        "commentCount": _int_or_none(album.comment_count),
        "description": getattr(album, "description", "") or "",
        "episodeCount": len(episodes),
        "episodes": episodes,
        "isFavorite": bool(getattr(album, "is_favorite", False)),
        "liked": bool(getattr(album, "liked", False)),
    }
    data.update(base_entity_fields(album))
    return data


def photo_to_dict(photo, include_image_urls: bool = False) -> Dict[str, Any]:
    try:
        images = list(photo)
    except Exception:
        images = []

    data = {
        "kind": "photo",
        "id": str(photo.photo_id),
        "title": photo.name,
        "albumId": str(photo.album_id),
        "index": _int_or_none(getattr(photo, "album_index", None)),
        "author": getattr(photo, "author", None),
        "tags": list(getattr(photo, "tags", []) or []),
        "imageCount": len(images),
    }
    if include_image_urls:
        data["imageUrls"] = [img.download_url for img in images]
    data.update(base_entity_fields(photo))
    return data


def page_to_dict(page, include_tags: bool = False) -> Dict[str, Any]:
    works = []
    if include_tags:
        for aid, title, tags in page.iter_id_title_tag():
            works.append({"id": str(aid), "title": title, "tags": list(tags or [])})
    else:
        for aid, title in page.iter_id_title():
            works.append({"id": str(aid), "title": title})

    total = getattr(page, "total", None)
    return {
        "page": getattr(page, "page_number", None),
        "total": _int_or_none(total) if total is not None else None,
        "pageCount": _int_or_none(getattr(page, "page_count", None)),
        "count": len(works),
        "works": works,
    }


def comment_to_dict(comment) -> Dict[str, Any]:
    return {
        "commentId": str(getattr(comment, "comment_id", "")),
        "albumId": str(getattr(comment, "album_id", "") or ""),
        "userId": str(getattr(comment, "user_id", "") or ""),
        "nickname": getattr(comment, "nickname", None) or getattr(comment, "username", None),
        "content": getattr(comment, "content", ""),
        "isSpoiler": bool(getattr(comment, "is_spoiler", False)),
        "likes": _int_or_none(getattr(comment, "likes", None)),
        "createdAt": str(getattr(comment, "created_at", "") or ""),
        "replies": [comment_to_dict(r) for r in (getattr(comment, "replies", None) or [])],
    }


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #

@dataclass
class DownloadSettings:
    """Everything a download needs, with no UI or CLI types."""

    targets: Sequence[str] = field(default_factory=list)
    kind: str = "album"                 # 'album' | 'photo'
    exports: List[str] = field(default_factory=list)   # subset of EXPORT_KINDS
    save_dir: Optional[str] = None
    threads: int = 30
    proxy: str = ""
    client_impl: Optional[str] = None   # 'api' | 'html'
    http_backend: Optional[str] = None
    option_file: Optional[str] = None
    dir_rule: str = "Bd / Aid / Ptitle"
    cache: bool = True
    image_suffix: Optional[str] = None


def pillow_codecs() -> str:
    """
    One-line report of the image codecs the installed Pillow can actually use.

    Why this is worth a startup log line: on Android this is a BUILD-time property,
    not a runtime one. p4a's Pillow recipe compiles the WebP codec only when
    `libwebp` is in the recipe build order (see buildozer.spec), and JM serves its
    page images as .webp. A build without it downloads every image successfully and
    then fails to decode all of them:

        PIL.UnidentifiedImageError: cannot identify image file <_io.BytesIO ...>

    which the UI reports only as "共 N 个图片下载失败". Printing the codec table at
    startup makes that a five-second diagnosis from `adb logcat -s python:D`.
    """
    try:
        import PIL
        from PIL import features
    except Exception as exc:  # pragma: no cover - Pillow is a hard dependency
        return f"unavailable ({exc})"

    parts = []
    for name in ("webp", "jpg", "zlib", "libtiff", "freetype2"):
        try:
            parts.append(f"{name}={'yes' if features.check(name) else 'NO'}")
        except Exception:
            parts.append(f"{name}=?")
    return f"Pillow {getattr(PIL, '__version__', '?')} " + " ".join(parts)


def missing_export_dependencies(exports: Iterable[str]) -> List[str]:
    """Names of absent optional modules needed by the requested export formats."""
    missing: List[str] = []
    for suffix in exports:
        for module in EXPORT_DEPENDENCIES.get(suffix, ()):
            try:
                __import__(module)
            except Exception:
                missing.append(f"{suffix}:{module}")
    return missing


def build_feature(jmcomic, exports: Sequence[str]):
    """
    Compose the jmcomic Feature chain for the export formats jmcomic should handle.

    `pdf` is NOT delegated to jmcomic: its PDF feature needs img2pdf, which cannot be
    installed on Android (pikepdf has no android wheel). PDF is produced by
    `_export_pdf_with_pillow` after the download instead.
    """
    extra = None
    for suffix in exports:
        if suffix == "pdf":
            continue
        feature = getattr(jmcomic.Feature, EXPORT_KINDS[suffix][0])
        extra = feature if extra is None else (extra + feature)
    return extra


def _safe_filename(text: str, fallback: str) -> str:
    """Make `text` usable as a filename on every platform we ship to."""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "")).strip(" .")
    return cleaned[:120] or fallback


def _export_pdf_with_pillow(image_paths: Sequence[str], output_dir: Path,
                            album_id: str, title: str,
                            progress_sink=None) -> str:
    """
    Write one multi-page PDF from the downloaded images, using Pillow only.

    Why not img2pdf: it hard-depends on `pikepdf`, a QPDF binding that has neither an
    android wheel nor a python-for-android recipe, so the Android build cannot install
    it. Pillow is already required for image handling and can write multi-page PDFs,
    which keeps PDF export working on every platform from one code path.

    Returns the PDF path.
    """
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise OperationError(
            "PDF export needs Pillow, which is not installed",
            hint="pip install Pillow",
        ) from e

    pages = [Path(p) for p in image_paths if Path(p).is_file()]
    if not pages:
        raise OperationError(
            "no downloaded images to build a PDF from",
            hint="The download may have produced no images for this id.",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{_safe_filename(f'[JM{album_id}]{title}', f'JM{album_id}')}.pdf"

    opened = []
    try:
        for path in pages:
            image = Image.open(path)
            # PDF cannot carry alpha/16-bit/palette pages directly.
            if image.mode in ("RGBA", "LA", "P", "PA"):
                image = image.convert("RGB")
            elif image.mode not in ("RGB", "L", "1"):
                image = image.convert("RGB")
            opened.append(image)

        first, rest = opened[0], opened[1:]
        save_kwargs = {"resolution": 150.0}
        if rest:
            first.save(target, "PDF", save_all=True, append_images=rest, **save_kwargs)
        else:
            first.save(target, "PDF", **save_kwargs)
    finally:
        for image in opened:
            try:
                image.close()
            except Exception:
                pass

    if progress_sink:
        try:
            progress_sink(f"PDF 已导出（Pillow，{len(opened)} 页）: {target}")
        except Exception:
            pass
    return str(target)


def run_download(settings: DownloadSettings, jmcomic=None,
                 progress_sink=None) -> Dict[str, Any]:
    """
    Execute a download described by `settings` and return a JSON-safe report.

    `progress_sink(text)` receives human-readable progress lines when supplied.
    Raises OperationError for conditions the caller should show to the user.
    """
    jmcomic = jmcomic or load_jmcomic()

    def note(text: str) -> None:
        if progress_sink:
            try:
                progress_sink(text)
            except Exception:
                pass

    unknown = [s for s in settings.exports if s not in EXPORT_KINDS]
    if unknown:
        raise OperationError(
            f"unsupported export format(s): {', '.join(unknown)}",
            hint=f"Supported: {', '.join(sorted(EXPORT_KINDS))}",
        )

    parsed, bad = [], []
    for token in settings.targets:
        try:
            parsed.append(require_id(jmcomic, token))
        except OperationError:
            bad.append(token)
    if bad:
        note(f"[跳过] 无法识别的车号：{', '.join(bad)}")
    if not parsed:
        raise OperationError("no usable JM ids were supplied",
                             hint="Pass a bare number, 'JM<number>', or a JM URL.")

    missing = missing_export_dependencies(settings.exports)
    if missing:
        note(f"[提示] 缺少导出依赖 {', '.join(missing)}，勾选的导出可能不产出文件")

    option = build_option(jmcomic, settings.option_file, settings.client_impl,
                          settings.http_backend)

    if settings.save_dir:
        base_dir = Path(settings.save_dir).expanduser()
    else:
        base_dir = default_download_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    option.dir_rule.base_dir = str(base_dir)
    if settings.dir_rule:
        option.dir_rule.rule = settings.dir_rule

    try:
        option.download.threading.image = max(1, min(50, int(settings.threads)))
        option.download.cache = bool(settings.cache)
        if settings.image_suffix:
            option.download.image.suffix = settings.image_suffix
    except Exception:
        pass

    if settings.proxy:
        try:
            option.client.postman.meta_data.proxies = {
                "http": settings.proxy,
                "https": settings.proxy,
            }
        except Exception:
            note("[提示] 代理设置未能应用到此 HTTP 后端，已忽略")

    extra = build_feature(jmcomic, settings.exports)
    api = jmcomic.download_photo if settings.kind == "photo" else jmcomic.download_album
    target = parsed[0] if len(parsed) == 1 else parsed

    note(f"开始下载 {len(parsed)} 个目标（{settings.kind}）")
    try:
        value = api(target, option, extra=extra)
    except jmcomic.PartialDownloadFailedException as e:
        downloader = e.downloader
        raise OperationError(
            f"partial download failure: {e}",
            hint="Re-run the same download; already-fetched images are reused.",
        ) from e
    except jmcomic.MissingAlbumPhotoException as e:
        raise OperationError(
            f"JM id does not exist: {e.error_jmid}",
            hint="Confirm the id, or the album may require a logged-in account.",
        ) from e
    except jmcomic.JmcomicException as e:
        raise OperationError(f"jmcomic failed: {e}") from e

    suffixes = [EXPORT_KINDS[s][1] for s in settings.exports]
    payload = serialize_download_return(value, suffixes)
    payload["kind"] = settings.kind
    payload["requested"] = parsed
    payload["saveDir"] = str(base_dir)

    # PDF is produced here rather than through jmcomic's feature, because that feature
    # needs img2pdf (-> pikepdf), which is not installable on Android.
    if "pdf" in settings.exports:
        try:
            jobs_list = payload.get("results", [payload]) if "results" in payload else [payload]
            for job in jobs_list:
                images = job.get("imageFiles") or []
                if not images:
                    note(f"[提示] JM{job.get('id')} 没有图片，跳过 PDF 导出")
                    continue
                pdf_path = _export_pdf_with_pillow(
                    images, base_dir, str(job.get("id") or ""),
                    str(job.get("title") or ""), progress_sink=note,
                )
                job.setdefault("exportFiles", [])
                if pdf_path not in job["exportFiles"]:
                    job["exportFiles"].append(pdf_path)
        except OperationError as e:
            note(f"[提示] PDF 导出失败：{e.message}")
        except Exception as e:
            note(f"[提示] PDF 导出失败：{type(e).__name__}: {e}")

    return payload


# --------------------------------------------------------------------------- #
# download result -> plain dict
# --------------------------------------------------------------------------- #

def scan_for_exports(directory: Path, suffixes: List[str]) -> List[str]:
    """Best-effort listing of freshly written export files (no recursion)."""
    found: List[str] = []
    if not directory.is_dir():
        return found
    wanted = {s.lower().lstrip(".") for s in suffixes}
    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix.lower().lstrip(".") in wanted:
                found.append(str(entry))
    except OSError:
        pass
    return found


def manifest_export_paths(result, suffixes: List[str]) -> List[str]:
    manifest = getattr(result, "manifest", None)
    if manifest is None:
        return []
    paths: List[str] = []
    for suffix in suffixes:
        try:
            paths.extend(str(p) for p in manifest.get_export_filepath_list(suffix))
        except Exception:
            continue
    return paths


def download_result_to_dict(result, export_suffixes: List[str]) -> Dict[str, Any]:
    detail = result.detail
    data: Dict[str, Any] = {
        "id": str(detail.id),
        "title": getattr(detail, "name", None),
        "savePath": str(getattr(detail, "save_path", "") or ""),
        "durationSec": round(getattr(result, "duration", 0.0) or 0.0, 3),
    }

    manifest = getattr(result, "manifest", None)
    if manifest is not None:
        try:
            images = [str(p) for p in manifest.image_filepath_list]
            data["imageCount"] = len(images)
            data["imageFiles"] = images
        except Exception:
            pass

    exports = manifest_export_paths(result, export_suffixes)
    if not exports and export_suffixes:
        save_path = Path(data["savePath"]) if data["savePath"] else None
        if save_path is not None:
            parents = [save_path, save_path.parent, Path.cwd()]
            for parent in parents:
                exports.extend(scan_for_exports(parent, export_suffixes))
            seen = set()
            exports = [p for p in exports if not (p in seen or seen.add(p))]
    if export_suffixes:
        data["exportFiles"] = exports

    return data


def serialize_download_return(value, export_suffixes: List[str]) -> Dict[str, Any]:
    """`download_album` returns DownloadResult for one id, BatchResult for many."""
    if hasattr(value, "failed") and hasattr(value, "total"):
        succeeded = [download_result_to_dict(r, export_suffixes) for r in value]
        failed = {str(k): str(v) for k, v in value.failed.items()}
        return {
            "total": _int_or_none(value.total),
            "succeeded": len(succeeded),
            "allSucceeded": bool(value.all_succeeded),
            "results": succeeded,
            "failed": failed,
        }
    return download_result_to_dict(value, export_suffixes)


# --------------------------------------------------------------------------- #
# environment report
# --------------------------------------------------------------------------- #

def doctor_report(probe_album_id: Optional[str] = None,
                  jmcomic=None, option_file: Optional[str] = None,
                  client_impl: Optional[str] = None,
                  http_backend: Optional[str] = None) -> Dict[str, Any]:
    """Collect an environment/connectivity report. Network probe is optional."""
    jmcomic = jmcomic or load_jmcomic()
    report: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
        "android": is_android(),
        "jmcomicVersion": getattr(jmcomic, "__version__", "unknown"),
        "httpBackend": http_backend or default_http_backend(),
        "optionPathEnv": os.environ.get("JM_OPTION_PATH"),
    }

    deps = {}
    # img2pdf is intentionally absent: PDF export no longer uses it (it depends on
    # pikepdf, which cannot be installed on Android), so reporting it would be noise.
    for module in ("PIL", "pyzipper", "py7zr", "psutil", "requests", "curl_cffi"):
        try:
            __import__(module)
            deps[module] = True
        except Exception:
            deps[module] = False
    report["optionalDeps"] = deps

    if probe_album_id:
        try:
            option = build_option(jmcomic, option_file, client_impl, http_backend)
            client = new_client(jmcomic, option)
            album = client.get_album_detail(probe_album_id)
            report["networkOk"] = True
            report["probe"] = {"id": str(album.id), "title": album.name}
        except Exception as e:
            report["networkOk"] = False
            report["probeError"] = str(e)

    return report


def export_default_option_yaml(jmcomic, option_file: Optional[str] = None,
                               client_impl: Optional[str] = None) -> str:
    """Serialize jmcomic's effective default option to YAML text."""
    option = build_option(jmcomic, option_file, client_impl, None)
    handle, tmp_name = tempfile.mkstemp(suffix=".yml", prefix="jm-option-")
    os.close(handle)
    try:
        option.to_file(tmp_name)
        return Path(tmp_name).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
