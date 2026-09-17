#!/usr/bin/env python3
"""
jmctl - a JSON-first command line wrapper around jmcomic (JMComic-Crawler-Python).

This file is the CLI front end. The actual engine lives in `jmcore.py`, which is
shared with the tkinter GUI and the Kivy GUI (the latter is what runs on Android,
Linux and macOS). Keeping the engine out of here is what makes the mobile builds
possible: a Kivy app has no terminal and no argparse.

Contract:
    python jmctl.py <command> [options]

* every command writes ONE JSON object to stdout;
* progress and library logging stay on stderr;
* exit code is 0 on success, 1 on a handled failure (with `"status": "error"`).

Upstream project: https://github.com/hect0x7/JMComic-Crawler-Python
Requires: python >= 3.9 and `pip install jmcomic`
Optional extra deps for exports: `pip install "jmcomic[plugins]" img2pdf`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Work both as `python scripts/jmctl.py` and as `import jmctl`.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import jmcore
except ImportError as exc:  # pragma: no cover - packaging guard
    raise SystemExit(f"jmcore.py must sit beside jmctl.py in {_HERE}: {exc}") from exc

# Re-exported for the GUIs and for backward compatibility with earlier jmctl users.
from jmcore import (  # noqa: F401
    ANDROID_HTTP_BACKEND,
    DEFAULT_HTTP_BACKEND,
    EXPORT_KINDS,
    EXPORT_DEPENDENCIES,
    SCHEMA_VERSION,
    DownloadSettings,
    OperationError,
    album_to_dict,
    apply_http_backend,
    attach_log_sink,
    build_option,
    comment_to_dict,
    detach_log_sink,
    download_result_to_dict,
    is_android,
    load_jmcomic,
    new_client,
    page_to_dict,
    photo_to_dict,
    require_id,
    route_logs_to_stderr,
    run_download,
    serialize_download_return,
    split_id_text,
)


def emit(payload, pretty: bool = False, stream=None) -> None:
    stream = stream or sys.stdout
    if pretty:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        stream.write(json.dumps(payload, ensure_ascii=False, default=str))
    stream.write("\n")
    stream.flush()


def ok(**fields):
    return {"status": "ok", "schemaVersion": SCHEMA_VERSION, **fields}


def fail(message: str, hint=None, **fields):
    payload = {"status": "error", "schemaVersion": SCHEMA_VERSION,
               "error": message, **fields}
    if hint:
        payload["hint"] = hint
    return payload


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_info(args, jmcomic):
    option = build_option(jmcomic, args.option, args.client_impl, args.http_backend)
    client = new_client(jmcomic, option)

    results = []
    for raw in args.ids:
        jm_id = require_id(jmcomic, raw)
        if args.kind == "photo":
            entity = client.get_photo_detail(jm_id, args.fetch_album)
            results.append(photo_to_dict(entity, include_image_urls=args.image_urls))
        else:
            results.append(album_to_dict(client.get_album_detail(jm_id)))

    if len(results) == 1:
        return ok(**results[0])
    return ok(count=len(results), items=results)


def cmd_search(args, jmcomic):
    option = build_option(jmcomic, args.option, args.client_impl, args.http_backend)
    client = new_client(jmcomic, option)

    modes = {
        "tag": client.search_tag,
        "work": client.search_work,
        "actor": client.search_actor,
        "site": client.search_site,
    }
    page = modes[args.mode](
        args.query,
        page=args.page,
        order_by=args.order_by,
        time=args.time,
        category=args.category,
        sub_category=args.sub_category,
    )
    return ok(query=args.query, mode=args.mode, **page_to_dict(page, include_tags=True))


def cmd_rank(args, jmcomic):
    option = build_option(jmcomic, args.option, args.client_impl, args.http_backend)
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
    return ok(period=args.period, category=args.category,
              **page_to_dict(page, include_tags=True))


def cmd_comments(args, jmcomic):
    option = build_option(jmcomic, args.option, args.client_impl, args.http_backend)
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
        pageCount=jmcore._int_or_none(getattr(page, "page_count", None)),
        total=jmcore._int_or_none(total) if total is not None else None,
        count=len(comments),
        comments=comments,
    )


def cmd_download(args, jmcomic):
    settings = DownloadSettings(
        targets=args.ids,
        kind=args.kind,
        exports=[s.lower().lstrip(".") for s in (args.export or [])],
        save_dir=args.save_dir,
        threads=args.threads,
        proxy=args.proxy or "",
        client_impl=args.client_impl,
        http_backend=args.http_backend,
        option_file=args.option,
    )
    return ok(**run_download(settings, jmcomic=jmcomic,
                             progress_sink=lambda t: print(t, file=sys.stderr)))


def cmd_config(args, jmcomic):
    if args.write:
        target = Path(args.write).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        option = build_option(jmcomic, args.option, args.client_impl, None)
        option.to_file(str(target))
        return ok(written=str(target), clientImpl=option.client.impl)

    text = jmcore.export_default_option_yaml(jmcomic, args.option, args.client_impl)
    option = build_option(jmcomic, args.option, args.client_impl, None)
    return ok(clientImpl=option.client.impl, yaml=text)


def cmd_doctor(args, jmcomic):
    report = jmcore.doctor_report(
        probe_album_id=None if args.no_network else args.probe_id,
        jmcomic=jmcomic,
        option_file=args.option,
        client_impl=args.client_impl,
        http_backend=args.http_backend,
    )
    return ok(**report)


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #

def add_common(p) -> None:
    p.add_argument("--option",
                   default=__import__("os").environ.get("JM_OPTION_PATH"),
                   help="Path to an option.yml. Defaults to $JM_OPTION_PATH, else "
                        "jmcomic defaults.")
    p.add_argument("--client-impl", choices=["html", "api"], help="Override client.impl.")
    p.add_argument("--http-backend", default=None,
                   choices=["curl_cffi", "curl_cffi_session", "requests",
                            "requests-session"],
                   help="HTTP backend. Defaults to curl_cffi (requests on Android, "
                        "where curl_cffi has no python-for-android recipe).")
    p.add_argument("--pretty", action="store_true", help="Indent the JSON output.")


def add_search_filters(p) -> None:
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--order-by", default="mr",
                   help="mr latest (default), mv views, mp pictures, tf likes, "
                        "tr score, md comments.")
    p.add_argument("--time", default="a", help="a all (default), t today, w week, m month.")
    p.add_argument("--category", default="0",
                   help="0 all, doujin, single, short, another, hanman, meiman, "
                        "doujin_cosplay, 3D, english_site.")
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
    p_search.add_argument("--mode", choices=["site", "tag", "work", "actor"],
                          default="site",
                          help="site = in-site search, tag/work/actor = that field.")
    add_search_filters(p_search)
    add_common(p_search)

    p_rank = sub.add_parser("rank", help="Rankings and category browsing.")
    p_rank.add_argument("--period", choices=["day", "week", "month", "custom"],
                        default="week")
    p_rank.add_argument("--page", type=int, default=1)
    p_rank.add_argument("--category", default="0")
    p_rank.add_argument("--time", default="a", help="Only used with --period custom.")
    p_rank.add_argument("--order-by", default="mv", help="Only used with --period custom.")
    p_rank.add_argument("--sub-category", default=None, help="html impl only.")
    add_common(p_rank)

    p_comments = sub.add_parser("comments", help="Read album or site-wide comments.")
    p_comments.add_argument("--id", default=None, help="Album id (omit with --forum).")
    p_comments.add_argument("--forum", action="store_true",
                            help="Read site-wide latest comments.")
    p_comments.add_argument("--page", type=int, default=1)
    add_common(p_comments)

    p_dl = sub.add_parser("download", help="Download albums/photos, optionally exporting.")
    p_dl.add_argument("ids", nargs="+", help="JM ids, 'JM123' strings, or JM URLs.")
    p_dl.add_argument("--kind", choices=["album", "photo"], default="album")
    p_dl.add_argument("--export", nargs="+", choices=sorted(EXPORT_KINDS), default=None,
                      help="Export formats produced after the download.")
    p_dl.add_argument("--save-dir", default=None,
                      help="Destination root. Defaults to ~/Downloads/JMComic.")
    p_dl.add_argument("--threads", type=int, default=30,
                      help="Concurrent images (1-50).")
    p_dl.add_argument("--proxy", default=None,
                      help="HTTP proxy, e.g. 127.0.0.1:7890.")
    add_common(p_dl)

    p_cfg = sub.add_parser("config", help="Print jmcomic's default option.yml.")
    p_cfg.add_argument("--write", default=None, help="Write it to this path.")
    add_common(p_cfg)

    p_doc = sub.add_parser("doctor",
                           help="Report interpreter, jmcomic, deps, and connectivity.")
    p_doc.add_argument("--probe-id", default="438696",
                       help="Album id used for the connectivity probe.")
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


def main(argv=None) -> int:
    jmcore.reconfigure_streams_utf8()

    args = build_parser().parse_args(argv)
    pretty = bool(getattr(args, "pretty", False))

    try:
        jmcomic = load_jmcomic()
        jmcore.route_logs_to_stderr()
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
