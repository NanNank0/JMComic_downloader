#!/usr/bin/env python3
"""
jmcore - the platform-independent JMComic download engine.

`jmctl.py` (CLI), `gui/app.py` (tkinter) and `gui/kivy_app.py` (Kivy, used on
Android/Linux/macOS) are all thin front ends over this module. Everything here is
importable without a GUI toolkit and without a terminal, which is what makes the
Android build possible: Kivy apps have neither.

Android-specific note
---------------------
`jmcomic` declares `curl-cffi` as a hard dependency, but python-for-android has no
recipe for it. curl-cffi exists to impersonate a browser's TLS fingerprint, and the
JM endpoints do not require it — verified by downloading a full chapter through the
pure-Python `requests` backend. So on Android we select that backend and let the
packaging step install jmcomic with `--no-deps`. `curl_cffi` is imported lazily by
commonX, so as long as the requests backend is used it is never touched.
"""

from __future__ import annotations

import logging
import os
import re
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
EXPORT_DEPENDENCIES: Dict[str, tuple] = {
    "pdf": ("img2pdf",),
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

def is_android() -> bool:
    """
    Detect Android without importing anything optional.

    python-for-android sets ANDROID_ARGUMENT and friends; `platform` may also
    report 'android' or 'linux' on the same device, so the env vars are the
    reliable signal.
    """
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE"):
        return True
    try:
        import platform

        return "android" in platform.platform().lower()
    except Exception:
        return False


def default_http_backend() -> str:
    return ANDROID_HTTP_BACKEND if is_android() else DEFAULT_HTTP_BACKEND


def load_jmcomic():
    """Import jmcomic, or raise a handled error explaining how to install it."""
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
    """A sensible per-platform default download location."""
    if is_android():
        # Kivy exposes the app's external files dir here; fall back to home.
        for key in ("ANDROID_PRIVATE", "ANDROID_APP_PATH"):
            base = os.environ.get(key)
            if base:
                return Path(base) / "downloads"
        return Path.home() / "downloads"
    return Path.home() / "Downloads" / "JMComic"


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
    """Compose the jmcomic Feature chain for the requested export formats."""
    extra = None
    for suffix in exports:
        feature = getattr(jmcomic.Feature, EXPORT_KINDS[suffix][0])
        extra = feature if extra is None else (extra + feature)
    return extra


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
    for module in ("img2pdf", "PIL", "pyzipper", "py7zr", "psutil",
                   "requests", "curl_cffi", "kivy"):
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
