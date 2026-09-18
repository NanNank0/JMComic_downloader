---
name: jmcomic
description: Use jmcomic (JMComic-Crawler-Python) to query JMComic metadata (album/photo details, search, rankings, comments) and to download albums or chapters, optionally exporting them to PDF, ZIP, or a long image. All commands return a single JSON object from one stable CLI entry point.
whenToUse: Use when the user supplies a JMComic album/photo id ("车号", JM123, or an 18comic album URL) and wants its metadata, wants to search/browse/rank JMComic works, wants comments, or wants to download/export an album or chapter. Do not use when the user is only asking about the upstream library's source code.
metadata:
  upstream: https://github.com/hect0x7/JMComic-Crawler-Python
  cli: scripts/jmctl.py
---

# JMComic via `jmctl.py`

This skill drives **JMComic-Crawler-Python** (`jmcomic`) through one wrapper script that
converts the library into a stable JSON contract. The same engine (`scripts/jmcore.py`)
also backs a graphical app shipped for Windows / Linux / macOS / Android, so anything you
can do here the user can also do by clicking.

## The one rule

**Call `scripts/jmctl.py` for everything. Never import `jmcomic` directly.**

The wrapper exists because `jmcomic`'s own CLI prints human-oriented text and the library
**logs progress to stdout**, which corrupts machine parsing. `jmctl.py` reroutes all
library logging to stderr and writes exactly one JSON object to stdout.

```bash
python <skill-dir>/scripts/jmctl.py <command> [options]
```

Resolve `<skill-dir>` to this skill's own directory. Every command prints one JSON object
to stdout: `{"status": "ok", ...}` or `{"status": "error", "error": "...", "hint": "..."}`.
Exit code is `0` on success and `1` on a handled failure. Library progress stays on stderr.

Because stderr carries the live progress log, run long downloads as a background job
rather than blocking, and read stdout only when the job settles.

## Step 0 — check the environment before anything else

```bash
python <skill-dir>/scripts/jmctl.py doctor
```

This reports the interpreter, the installed `jmcomic` version, which optional export
dependencies are present, and whether the site is reachable. If `jmcomic` is missing:

```bash
python -m pip install jmcomic
```

Install into **the interpreter that runs the script**; `doctor` prints its exact path, so
prefer `"<that path>" -m pip install jmcomic` when several Pythons exist. Exports need
extra packages — install only what the requested format needs:

| Format | Package |
|---|---|
| PDF | `Pillow` |
| ZIP | `pyzipper` (or `py7zr` for 7z) |
| long image | `Pillow` |

`pip install "jmcomic[plugins]"` installs the whole set. A missing dependency makes the
download succeed but the export silently absent, so check `exportFiles` in the result.

If `doctor` reports `networkOk: false`, do not retry blindly: switch `--client-impl`
between `api` (default) and `html`, or configure a proxy in an `option.yml`
(see [Configuration](#configuration)). Never guess a replacement domain — let the
library resolve one.

## Commands

### `info` — metadata, no download

```bash
jmctl.py info 438696
jmctl.py info JM438696 https://18comic.vip/album/438696 --pretty
jmctl.py info 438696 --kind photo --image-urls
```

Accepts bare numbers, `JM123` strings, or JM URLs. `--kind album` (default) returns
`title`, `authors`, `tags`, `works`, `actors`, `pageCount`, `pubDate`, `updateDate`,
`likes`, `views`, `commentCount`, and an `episodes` list. `--kind photo` describes one
chapter; add `--image-urls` to also list every page URL.

An empty `episodes` list is normal for a single-chapter album — the album is then its own
chapter, and its id doubles as the photo id.

### `search` — find albums

```bash
jmctl.py search "MANA"
jmctl.py search "無修正" --mode tag --page 1
jmctl.py search "原神" --mode work
jmctl.py search "神里绫华" --mode actor
```

`--mode`: `site` (default, the site's own search), `tag`, `work`, `actor`.
Filters: `--page`, `--order-by` (`mr` latest, `mv` views, `mp` pictures, `tf` likes,
`tr` score, `md` comments), `--time` (`a` all, `t` today, `w` week, `m` month),
`--category` (`0` all, `doujin`, `single`, `short`, `another`, `hanman`, `meiman`,
`doujin_cosplay`, `3D`, `english_site`), `--sub-category` (html impl only, e.g.
`chinese`, `japanese`, `CG`, `cosplay`, `youth`, `3d`).

`+tag` requires a tag and `-tag` excludes it: `"+全彩 +人妻"`.

Returns `total`, `pageCount`, `page`, and `works[]` of `{id, title, tags}`. Feed an
`id` from here straight into `info` or `download`.

> `total` is the site's reported result count. When it disagrees with
> `count × pageCount`, trust `works[]` — the site's counter is approximate.

### `rank` — rankings and category browsing

```bash
jmctl.py rank --period day
jmctl.py rank --period week --category doujin
jmctl.py rank --period custom --time m --order-by tf --category hanman
```

`--period` is `day`, `week`, `month`, or `custom` (which uses `--time` and `--order-by`
to build an arbitrary category query). Same result shape as `search`.

### `comments`

```bash
jmctl.py comments --id 438696
jmctl.py comments --forum --page 1
```

Returns `comments[]` with `content`, `nickname`, `likes`, `isSpoiler`, `createdAt`, and
nested `replies[]`. `--forum` reads site-wide comments; each then carries its `albumId`.

### `download` — fetch and optionally export

```bash
jmctl.py download 438696
jmctl.py download 438696 --export pdf
jmctl.py download 438696 123456 --export pdf zip
jmctl.py download 438696 --kind photo
```

`--kind album` (default) downloads every chapter; `--kind photo` downloads one chapter.
`--export` accepts `pdf`, `zip`, `png` (long image) and may combine them. Multiple ids
are downloaded concurrently and reported individually; one failure does not abort the
rest.

Result for a single id: `id`, `title`, `savePath`, `durationSec`, `imageCount`,
`imageFiles[]`, plus `exportFiles[]` when `--export` was used. Multiple ids return
`{total, succeeded, allSucceeded, results[], failed{}}`.

Downloaded files are cached on disk, so re-running the same command is cheap and is the
correct response to a partial failure — completed images are reused. Incremental
"only new chapters" behaviour needs the upstream `find_update` plugin and is out of scope
here.

### `config` — inspect the configuration baseline

```bash
jmctl.py config                       # print the effective default option.yml
jmctl.py config --write ./option.yml  # write a starter file you can then edit
```

### `doctor`

```bash
jmctl.py doctor
jmctl.py doctor --no-network          # interpreter/deps only, no network probe
```

## Configuration

Three layers, most specific first:

1. **CLI flags** — `--client-impl`, and everything under `download`.
2. **`option.yml`** — passed with `--option <path>` or `$JM_OPTION_PATH`. This is where
   proxy, cookies, download directory, threading, and image conversion belong.
3. **jmcomic defaults.**

Generate a baseline with `jmctl.py config --write ./option.yml`, then keep only the keys
you change. The keys that matter in practice:

```yaml
client:
  impl: api                 # api (default, mobile) or html (web, richer search filters)
  retry_times: 5
  postman:
    meta_data:
      proxies: system       # or null, or "127.0.0.1:7890", or {http: ..., https: ...}
      cookies:
        AVS: <value>        # only needed for login-gated albums; must match the domain used

download:
  cache: true               # reuse images already on disk
  image:
    decode: true            # JM images are scrambled; keep true unless you want raw files
    suffix: null            # e.g. .jpg to also convert format
  threading:
    image: 30               # concurrent images (site allows <= 50)
    photo: 16               # concurrent chapters

dir_rule:
  base_dir: ./downloads     # where albums are written
  rule: Bd / Aid / Ptitle   # path template; Bd=base_dir, A*=album field, P*=photo field
```

`dir_rule.rule` composes from album (`A`) and chapter (`P`) fields, e.g.
`Bd / Aauthor / (JM{Aid}-{Pindex})-{Pname}`. Which fields exist is discoverable at runtime
with `jmctl.py info <id> --pretty`.

Cookies are domain-scoped: a cookie from one JM domain has no effect on another. Pass
credentials only via `option.yml` or `$JM_OPTION_PATH`; never put them on the command line,
and never echo them back into the conversation.

Login-gated features (favourites, check-in, favourite export) are deliberately **not**
wrapped by this skill. When a user needs them, use the library's `login` plugin through a
custom `option.yml` rather than extending this CLI ad hoc.

## Reporting results to the user

- Quote the `title`, the JM id, and the absolute `savePath` so the user can open the files.
- `likes` and `views` are strings the site formats (e.g. `"77K"`), not integers.
- `updateDate` of `"0"` means the site reported no update date; report it as unknown.
- To show a cover, use `https://18comic.vip/album/<id>/` rather than inventing a CDN URL.
- Downloading is subject to the site's own rate limits. Do not raise `threading.image` above
  50, do not fan out many albums at once, and never loop `download` over a large id list —
  pass the ids to a single `download` invocation so one option and one client are reused.
- If the user would rather click than read command output, point them at the bundled GUI:
  `python gui/app.py`, or the packaged `dist/jmcomic-downloader.exe`. Both are front ends
  over this same engine, so behavior and configuration are identical.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `jmcomic is not importable` | Install into the interpreter that runs the script, using the path `doctor` prints. |
| `could not parse a JM id` | The id is non-numeric. Extract the digits from the user's text first. |
| `MissingAlbumPhotoException` | Wrong id, or a login-gated album. Confirm the id before assuming it needs cookies. |
| `networkOk: false` | Try the other `--client-impl`, or set a proxy in `option.yml`. The library picks working domains itself. |
| Download ok but `exportFiles` empty | The format's dependency is missing (see Step 0), or the id produced no images. |
| `partial download failure` | Re-run the identical command; cached images are reused. Check the `failedPhotos`/`failedImages` fields. |
| JSON parse error from stdout | Something wrote to stdout. Confirm you invoked `jmctl.py` and not `jmcomic`/`jmv`. |

## Security and privacy

This skill downloads adult content. Keep it to what the user asked for, do not
auto-download from search results without confirmation, and do not write credentials or
cookies into anything you report back.
