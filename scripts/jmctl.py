#!/usr/bin/env python3
"""
jmctl - a JSON-first command line wrapper around jmcomic (JMComic-Crawler-Python).

Why this exists
---------------
`jmcomic` is a Python library. Its own CLI (`jmcomic` / `jmv`) prints human-oriented
text, which is awkward for an agent to parse. This wrapper exposes the same engine
through one stable contract:

    python jmctl.py <command> [options]

* every command writes ONE JSON object to stdout;
* progress and library logging stay on stderr;
* exit code is 0 on success, 1 on a handled failure (with `"status": "error"`).

Upstream project: https://github.com/hect0x7/JMComic-Crawler-Python
Requires: python >= 3.9 and `pip install jmcomic`
Optional extra deps for exports: `pip install "jmcomic[plugins]" img2pdf`

Only JSON-safe leaf fields are ever placed in the output; no live library objects
are serialized.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# Suffix -> (Feature attribute name, output file extension)
EXPORT_KINDS: Dict[str, tuple] = {
    "pdf": ("export_pdf", "pdf"),
    "zip": ("export_zip", "zip"),
    "png": ("export_long_img", "png"),
}


class OperationError(Exception):
    """A failure that should be reported as JSON rather than a traceback."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def emit(payload: Dict[str, Any], pretty: bool = False, stream=None) -> None:
    stream = stream or sys.stdout
    if pretty:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        stream.write(json.dumps(payload, ensure_ascii=False, default=str))
    stream.write("\n")
    stream.flush()


def ok(**fields: Any) -> Dict[str, Any]:
    return {"status": "ok", "schemaVersion": SCHEMA_VERSION, **fields}


def fail(message: str, hint: Optional[str] = None, **fields: Any) -> Dict[str, Any]:
    payload = {"status": "error", "schemaVersion": SCHEMA_VERSION, "error": message, **fields}
    if hint:
        payload["hint"] = hint
    return payload


# --------------------------------------------------------------------------- #
# jmcomic loading
# --------------------------------------------------------------------------- #

def load_jmcomic():
    """Import jmcomic, or raise a handled error explaining how to install it."""
    try:
        import jmcomic  # noqa: F401
    except ImportError as e:
        raise OperationError(
            f"jmcomic is not importable: {e}",
            hint=(
                f'Install it into the interpreter you are running: '
                f'"{sys.executable}" -m pip install jmcomic'
            ),
        ) from e
    return jmcomic


def route_jm_logs_to_stderr() -> None:
    """
    jmcomic installs a console log handler on stdout, and the downloader is chatty.

    That would interleave progress lines with the single JSON object this CLI is
    contracted to write to stdout, so every existing handler is replaced with an
    explicit stderr handler. stderr stays fully available for progress and errors.
    """
    import logging

    logger = logging.getLogger("jmcomic")
    handler = logging.StreamHandler(sys.stderr)
    try:
        # sys.stderr was reconfigured to utf-8 in main(); reopen its descriptor the
        # same way so the library's CJK log text survives a legacy console codec.
        handler.setStream(
            open(sys.stderr.fileno(), "w", encoding="utf-8", errors="replace",
                 buffering=1, closefd=False)
        )
    except Exception:
        pass
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.handlers = [handler]
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    # jmcomic adds its stdout handler lazily on first log call; block that path.
    logger.propagate = False


def require_id(jmcomic, value: str) -> str:
    """Parse 'JM123', 'https://18comic.vip/album/123', '123' -> '123'."""
    try:
        return jmcomic.JmcomicText.parse_to_jm_id(value)
    except Exception as e:
        raise OperationError(
            f"could not parse a JM id out of {value!r}: {e}",
            hint="Pass a bare number, a 'JM<number>' string, or a JM URL.",
        ) from e


def build_option(jmcomic, option_file: Optional[str], client_impl: Optional[str]):
    """Create a JmOption from the config file (if any), then apply CLI overrides."""
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
            # de-duplicate while preserving order
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
# commands
# --------------------------------------------------------------------------- #

def cmd_info(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, args.client_impl)
    client = new_client(jmcomic, option)

    results = []
    for raw in args.ids:
        jm_id = require_id(jmcomic, raw)
        if args.kind == "photo":
            entity = client.get_photo_detail(jm_id, args.fetch_album)
            results.append(photo_to_dict(entity, include_image_urls=args.image_urls))
        else:
            entity = client.get_album_detail(jm_id)
            results.append(album_to_dict(entity))

    if len(results) == 1:
        return ok(**results[0])
    return ok(count=len(results), items=results)


def cmd_search(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, args.client_impl)
    client = new_client(jmcomic, option)

    if args.mode == "tag":
        page = client.search_tag(
            args.query,
            page=args.page,
            order_by=args.order_by,
            time=args.time,
            category=args.category,
            sub_category=args.sub_category,
        )
    elif args.mode == "work":
        page = client.search_work(
            args.query,
            page=args.page,
            order_by=args.order_by,
            time=args.time,
            category=args.category,
            sub_category=args.sub_category,
        )
    elif args.mode == "actor":
        page = client.search_actor(
            args.query,
            page=args.page,
            order_by=args.order_by,
            time=args.time,
            category=args.category,
            sub_category=args.sub_category,
        )
    else:
        page = client.search_site(
            args.query,
            page=args.page,
            order_by=args.order_by,
            time=args.time,
            category=args.category,
            sub_category=args.sub_category,
        )

    return ok(query=args.query, mode=args.mode, **page_to_dict(page, include_tags=True))


def cmd_rank(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, args.client_impl)
    client = new_client(jmcomic, option)

    if args.period == "day":
        page = client.day_ranking(args.page, args.category)
    elif args.period == "week":
        page = client.week_ranking(args.page, args.category)
    elif args.period == "month":
        page = client.month_ranking(args.page, args.category)
    else:
        page = client.categories_filter(
            page=args.page,
            time=args.time,
            category=args.category,
            order_by=args.order_by,
            sub_category=args.sub_category,
        )

    return ok(
        period=args.period,
        category=args.category,
        **page_to_dict(page, include_tags=True),
    )


def cmd_comments(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, args.client_impl)
    client = new_client(jmcomic, option)

    if args.forum:
        page = client.forum_pagination(page=args.page)
    else:
        if not args.id:
            raise OperationError("album comments need --id <album id>",
                                 hint="Or pass --forum to read site-wide comments.")
        page = client.album_pagination(require_id(jmcomic, args.id), page=args.page)

    comments = [comment_to_dict(c) for c in page]
    total = getattr(page, "total", None)
    return ok(
        page=getattr(page, "page_number", None),
        pageCount=_int_or_none(getattr(page, "page_count", None)),
        total=_int_or_none(total) if total is not None else None,
        count=len(comments),
        comments=comments,
    )


def cmd_download(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, args.client_impl)

    suffixes = [s.lower().lstrip(".") for s in (args.export or [])]
    unknown = [s for s in suffixes if s not in EXPORT_KINDS]
    if unknown:
        raise OperationError(
            f"unsupported export format(s): {', '.join(unknown)}",
            hint=f"Supported: {', '.join(sorted(EXPORT_KINDS))}",
        )

    extra = None
    if suffixes:
        for suffix in suffixes:
            feature = getattr(jmcomic.Feature, EXPORT_KINDS[suffix][0])
            extra = feature if extra is None else (extra + feature)

    jm_ids = [require_id(jmcomic, raw) for raw in args.ids]
    api = jmcomic.download_photo if args.kind == "photo" else jmcomic.download_album
    target = jm_ids[0] if len(jm_ids) == 1 else jm_ids

    try:
        value = api(target, option, extra=extra)
    except jmcomic.PartialDownloadFailedException as e:
        downloader = e.downloader
        raise OperationError(
            f"partial download failure: {e}",
            hint="Re-run the same command; already-downloaded images are reused from cache.",
            failedPhotos=[str(p) for p, _ in downloader.download_failed_photo],
            failedImages=[str(i) for i, _ in downloader.download_failed_image],
        ) from e
    except jmcomic.MissingAlbumPhotoException as e:
        raise OperationError(f"JM id does not exist: {e.error_jmid}") from e
    except jmcomic.JmcomicException as e:
        raise OperationError(f"jmcomic failed: {e}") from e

    payload = serialize_download_return(value, [EXPORT_KINDS[s][1] for s in suffixes])
    return ok(kind=args.kind, requested=jm_ids, **payload)


def cmd_config(args, jmcomic) -> Dict[str, Any]:
    option = build_option(jmcomic, args.option, None)
    if args.client_impl:
        option.client.impl = args.client_impl

    # JmOption serializes through to_file(); dump to a temp file when only printing.
    if args.write:
        target = Path(args.write).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        option.to_file(str(target))
        return ok(written=str(target), clientImpl=option.client.impl)

    import tempfile

    handle, tmp_name = tempfile.mkstemp(suffix=".yml", prefix="jm-option-")
    os.close(handle)
    try:
        option.to_file(tmp_name)
        text = Path(tmp_name).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    return ok(clientImpl=option.client.impl, yaml=text)


def cmd_doctor(args, jmcomic) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "jmcomicVersion": getattr(jmcomic, "__version__", "unknown"),
        "jmcomicPath": getattr(jmcomic, "__file__", ""),
        "optionPathEnv": os.environ.get("JM_OPTION_PATH"),
    }

    # Optional dependencies used by the export features.
    deps = {}
    for module in ("img2pdf", "PIL", "pyzipper", "py7zr", "psutil"):
        try:
            __import__(module)
            deps[module] = True
        except Exception:
            deps[module] = False
    report["optionalDeps"] = deps

    if not args.no_network:
        try:
            client = new_client(jmcomic, build_option(jmcomic, args.option, args.client_impl))
            album = client.get_album_detail(args.probe_id)
            report["networkOk"] = True
            report["probe"] = {"id": str(album.id), "title": album.name}
        except Exception as e:
            report["networkOk"] = False
            report["probeError"] = str(e)

    return ok(**report)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #

def add_common(p, *, option_default: Optional[str] = None) -> None:
    p.add_argument(
        "--option",
        default=option_default if option_default is not None else os.environ.get("JM_OPTION_PATH"),
        help="Path to an option.yml. Defaults to $JM_OPTION_PATH, else jmcomic defaults.",
    )
    p.add_argument("--client-impl", choices=["html", "api"], help="Override client.impl.")
    p.add_argument("--pretty", action="store_true", help="Indent the JSON output.")


def add_search_filters(p) -> None:
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--order-by", default="mr",
                   help="mr latest (default), mv views, mp pictures, tf likes, tr score, md comments.")
    p.add_argument("--time", default="a", help="a all (default), t today, w week, m month.")
    p.add_argument("--category", default="0",
                   help="0 all, doujin, single, short, another, hanman, meiman, doujin_cosplay, 3D, english_site.")
    p.add_argument("--sub-category", default=None,
                   help="html impl only, e.g. chinese, japanese, CG, cosplay, youth, 3d.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jmctl",
        description="JSON-first CLI around jmcomic (JMComic-Crawler-Python).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="Fetch album/photo metadata without downloading.")
    p_info.add_argument("ids", nargs="+", help="JM ids, 'JM123' strings, or JM URLs.")
    p_info.add_argument("--kind", choices=["album", "photo"], default="album")
    p_info.add_argument("--fetch-album", action="store_true",
                        help="For --kind photo, also request the parent album.")
    p_info.add_argument("--image-urls", action="store_true",
                        help="For --kind photo, include every image URL in the output.")
    add_common(p_info)

    p_search = sub.add_parser("search", help="Search albums.")
    p_search.add_argument("query")
    p_search.add_argument("--mode", choices=["site", "tag", "work", "actor"], default="site",
                          help="site = in-site search, tag/work/actor = search that field.")
    add_search_filters(p_search)
    add_common(p_search)

    p_rank = sub.add_parser("rank", help="Rankings and category browsing.")
    p_rank.add_argument("--period", choices=["day", "week", "month", "custom"], default="week")
    p_rank.add_argument("--page", type=int, default=1)
    p_rank.add_argument("--category", default="0")
    p_rank.add_argument("--time", default="a", help="Only used with --period custom.")
    p_rank.add_argument("--order-by", default="mv", help="Only used with --period custom.")
    p_rank.add_argument("--sub-category", default=None, help="html impl only.")
    add_common(p_rank)

    p_comments = sub.add_parser("comments", help="Read album comments or site-wide comments.")
    p_comments.add_argument("--id", default=None, help="Album id (omit with --forum).")
    p_comments.add_argument("--forum", action="store_true", help="Read site-wide latest comments.")
    p_comments.add_argument("--page", type=int, default=1)
    add_common(p_comments)

    p_dl = sub.add_parser("download", help="Download albums/photos and optionally export files.")
    p_dl.add_argument("ids", nargs="+", help="JM ids, 'JM123' strings, or JM URLs.")
    p_dl.add_argument("--kind", choices=["album", "photo"], default="album")
    p_dl.add_argument("--export", nargs="+", choices=sorted(EXPORT_KINDS), default=None,
                      help="Export formats produced after the download.")
    add_common(p_dl)

    p_cfg = sub.add_parser("config", help="Print jmcomic's default option.yml.")
    p_cfg.add_argument("--write", default=None, help="Write it to this path instead of stdout.")
    add_common(p_cfg)

    p_doc = sub.add_parser("doctor", help="Report interpreter, jmcomic, deps, and connectivity.")
    p_doc.add_argument("--probe-id", default="438696", help="Album id used for the connectivity probe.")
    p_doc.add_argument("--no-network", action="store_true", help="Skip the network probe.")
    add_common(p_doc)

    return parser


DISPATCH = {
    "info": cmd_info,
    "search": cmd_search,
    "rank": cmd_rank,
    "comments": cmd_comments,
    "download": cmd_download,
    "config": cmd_config,
    "doctor": cmd_doctor,
}


def main(argv: Optional[List[str]] = None) -> int:
    # Make the JSON contract robust on consoles whose default codec (e.g. cp936)
    # cannot encode CJK titles.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    args = build_parser().parse_args(argv)
    pretty = bool(getattr(args, "pretty", False))

    try:
        jmcomic = load_jmcomic()
        route_jm_logs_to_stderr()
        payload = DISPATCH[args.command](args, jmcomic)
        emit(payload, pretty=pretty)
        return 0
    except OperationError as e:
        emit(fail(e.message, e.hint), pretty=pretty, stream=sys.stderr)
        emit(fail(e.message, e.hint), pretty=pretty)
        return 1
    except KeyboardInterrupt:
        emit(fail("interrupted"), pretty=pretty)
        return 130
    except Exception as e:  # unexpected: still emit parseable JSON
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit(fail(f"{type(e).__name__}: {e}"), pretty=pretty)
        return 1


if __name__ == "__main__":
    sys.exit(main())
