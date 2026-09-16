#!/usr/bin/env python3
"""
JMComic Downloader - a small desktop window for downloading JM albums/chapters by id.

It is a thin tkinter front end over `jmctl.py`, so the GUI and the agent-facing CLI
share one code path and one configuration model.

Usage:
    python app.py                 # run from source
    (or launch the packaged jmcomic-downloader.exe)

Design notes:
  * Every download runs on a worker thread. tkinter widgets are touched ONLY from the
    main thread, so all worker output crosses a queue that the main loop drains.
  * jmcomic logs progress to stdout by default; jmctl reroutes it to stderr, and this
    app additionally attaches a handler that forwards log records into the window.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
from pathlib import Path

# jmctl.py lives in the sibling scripts/ directory. Support both "run from a checkout"
# and a frozen PyInstaller bundle (where the sources are unpacked side by side).
def _candidate_source_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    dirs = [here, here.parent / "scripts", here.parent]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        dirs = [meipass, meipass / "scripts", exe_dir, exe_dir / "scripts"] + dirs
    return dirs


for _candidate in _candidate_source_dirs():
    if (_candidate / "jmctl.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

try:
    import jmctl
except ImportError as exc:  # pragma: no cover - startup guard
    raise SystemExit(
        "jmctl.py not found. Keep app.py in gui/ beside scripts/jmctl.py, "
        f"or place it next to the executable. Searched: "
        f"{', '.join(str(p) for p in _candidate_source_dirs())} ({exc})"
    ) from exc

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# --------------------------------------------------------------------------- #
# log bridge: jmcomic records -> GUI queue
# --------------------------------------------------------------------------- #

class QueueLogHandler:
    """A logging.Handler that pushes formatted records onto the GUI queue."""

    def __init__(self, sink):
        import logging

        self._sink = sink
        self._handler = logging.Handler()
        self._handler.emit = self._emit
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
        )

    def _emit(self, record):
        try:
            self._sink(self._handler.format(record))
        except Exception:
            pass

    @property
    def handler(self):
        return self._handler


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #

class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()

        root.title("JMComic 下载器")
        root.minsize(720, 560)

        self._build_widgets()
        self._install_log_bridge()

        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._preflight()

    # -- UI construction ---------------------------------------------------- #

    def _build_widgets(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        # ---- input row
        box = ttk.LabelFrame(outer, text="下载目标", padding=8)
        box.pack(fill="x")

        ttk.Label(box, text="车号 / 链接：").grid(row=0, column=0, sticky="w")
        self.ids_var = tk.StringVar()
        entry = ttk.Entry(box, textvariable=self.ids_var)
        entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(4, 0))
        entry.focus_set()
        entry.bind("<Return>", lambda _e: self._start())

        ttk.Label(box, text="多个号码用空格或逗号分隔，也支持 JM123 和 18comic 链接",
                  foreground="#666666").grid(row=1, column=1, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Label(box, text="类型：").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.kind_var = tk.StringVar(value="album")
        kinds = ttk.Frame(box)
        kinds.grid(row=2, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Radiobutton(kinds, text="整本 (album)", value="album",
                        variable=self.kind_var).pack(side="left")
        ttk.Radiobutton(kinds, text="单章 (photo)", value="photo",
                        variable=self.kind_var).pack(side="left", padx=(12, 0))

        box.columnconfigure(1, weight=1)

        # ---- output settings
        opts = ttk.LabelFrame(outer, text="输出设置", padding=8)
        opts.pack(fill="x", pady=(8, 0))

        ttk.Label(opts, text="保存到：").grid(row=0, column=0, sticky="w")
        default_dir = Path.home() / "Downloads" / "JMComic"
        self.dir_var = tk.StringVar(value=str(default_dir))
        ttk.Entry(opts, textvariable=self.dir_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(opts, text="浏览…", command=self._choose_dir).grid(row=0, column=2)
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="导出：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        exports = ttk.Frame(opts)
        exports.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.export_vars = {}
        for suffix, label in (("pdf", "PDF"), ("zip", "ZIP"), ("png", "长图")):
            var = tk.BooleanVar(value=(suffix == "pdf"))
            self.export_vars[suffix] = var
            ttk.Checkbutton(exports, text=label, variable=var).pack(side="left")
        ttk.Label(exports, text="（PDF 需 img2pdf，长图需 Pillow）",
                  foreground="#666666").pack(side="left", padx=(10, 0))

        ttk.Label(opts, text="图片并发：").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.thread_var = tk.StringVar(value="30")
        ttk.Spinbox(opts, from_=1, to=50, width=5,
                    textvariable=self.thread_var).grid(row=2, column=1, sticky="w", pady=(6, 0))

        ttk.Label(opts, text="代理：").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.proxy_var = tk.StringVar(value="")
        ttk.Entry(opts, textvariable=self.proxy_var).grid(row=3, column=1, sticky="ew",
                                                          padx=4, pady=(6, 0))
        ttk.Label(opts, text="留空 = 跟随系统；如 127.0.0.1:7890",
                  foreground="#666666").grid(row=3, column=2, sticky="w", pady=(6, 0))

        # ---- actions
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(8, 0))
        self.start_btn = ttk.Button(actions, text="开始下载", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(actions, text="取消", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="打开保存目录", command=self._open_dir).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="清空日志", command=self._clear_log).pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))

        # ---- log
        logbox = ttk.LabelFrame(outer, text="日志", padding=6)
        logbox.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(logbox, height=14, wrap="word", state="disabled",
                           background="#111418", foreground="#d8dee9",
                           insertbackground="#d8dee9", relief="flat")
        scroll = ttk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.status = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status, anchor="w",
                  relief="sunken", padding=4).pack(fill="x", pady=(6, 0))

    # -- logging ------------------------------------------------------------ #

    def _install_log_bridge(self):
        import logging

        bridge = QueueLogHandler(self._log_line)
        logger = logging.getLogger("jmcomic")
        logger.addHandler(bridge.handler)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
        self._bridge = bridge  # keep a reference alive

        # Capture anything the worker thread prints as well.
        self._real_stdout = sys.stdout

    def _log_line(self, text: str):
        self.events.put(("log", text))

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- preflight ---------------------------------------------------------- #

    def _preflight(self):
        try:
            jmcomic = jmctl.load_jmcomic()
        except jmctl.OperationError as e:
            self._append_log(f"[错误] {e.message}")
            if e.hint:
                self._append_log(f"       {e.hint}")
            self.status.set("缺少依赖：jmcomic")
            self.start_btn.state(["disabled"])
            messagebox.showerror(
                "缺少依赖",
                f"{e.message}\n\n请先安装：\n{sys.executable} -m pip install jmcomic",
            )
            return

        missing = [name for name, mod in (("PDF 导出", "img2pdf"), ("长图导出", "PIL"))
                   if not self._has_module(mod)]
        version = getattr(jmcomic, "__version__", "unknown")
        self._append_log(f"jmcomic {version} 已就绪 · Python {sys.version.split()[0]}")
        if missing:
            self._append_log(f"提示：{'、'.join(missing)} 所需依赖缺失，对应导出可能不生效")
        self._append_log("填入车号后按回车或点“开始下载”。")

    @staticmethod
    def _has_module(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    # -- actions ------------------------------------------------------------ #

    def _choose_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if chosen:
            self.dir_var.set(chosen)

    def _open_dir(self):
        target = Path(self.dir_var.get()).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(target)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as e:
            messagebox.showwarning("无法打开目录", str(e))

    def _cancel(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status.set("正在取消…（当前图片完成后停止）")
            self._append_log("已请求取消，等待当前任务收尾…")

    def _start(self):
        if self.worker and self.worker.is_alive():
            return

        raw = self.ids_var.get().strip()
        if not raw:
            messagebox.showinfo("缺少车号", "请先输入至少一个车号。")
            return

        import re
        # Accept spaces, commas, semicolons, and newlines between ids.
        tokens = [t for t in re.split(r"[\s,;，、]+", raw) if t]
        if not tokens:
            messagebox.showinfo("缺少车号", "请先输入至少一个车号。")
            return

        try:
            threads = max(1, min(50, int(self.thread_var.get())))
        except ValueError:
            threads = 30

        exports = [s for s, var in self.export_vars.items() if var.get()]
        save_dir = self.dir_var.get().strip() or str(Path.home() / "Downloads" / "JMComic")

        self.cancel_flag.clear()
        self.start_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        self.progress.start(12)
        self.status.set("正在准备…")
        self._append_log("")
        self._append_log(f"===== 开始：{len(tokens)} 个目标 · 类型={self.kind_var.get()} "
                         f"· 导出={exports or '无'} =====")

        config = {
            "tokens": tokens,
            "kind": self.kind_var.get(),
            "exports": exports,
            "save_dir": save_dir,
            "threads": threads,
            "proxy": self.proxy_var.get().strip(),
        }
        self.worker = threading.Thread(target=self._run_download, args=(config,), daemon=True)
        self.worker.start()

    # -- worker ------------------------------------------------------------- #

    def _run_download(self, cfg):
        """Runs off the main thread. Never touches tkinter widgets directly."""
        jmcomic = None
        try:
            jmcomic = jmctl.load_jmcomic()
            option = self._build_option(jmcomic, cfg)

            parsed, bad = [], []
            for token in cfg["tokens"]:
                try:
                    parsed.append(jmctl.require_id(jmcomic, token))
                except jmctl.OperationError:
                    bad.append(token)
            if bad:
                self.events.put(("log", f"[跳过] 无法识别的车号：{', '.join(bad)}"))
            if not parsed:
                self.events.put(("error", "没有任何可用的车号。"))
                return

            extra = None
            for suffix in cfg["exports"]:
                feature = getattr(jmcomic.Feature, jmctl.EXPORT_KINDS[suffix][0])
                extra = feature if extra is None else (extra + feature)

            self.events.put(("status", f"正在下载 {len(parsed)} 个目标…"))

            if len(parsed) == 1:
                api = (jmcomic.download_photo if cfg["kind"] == "photo"
                       else jmcomic.download_album)
                value = api(parsed[0], option, extra=extra)
                result = jmctl.serialize_download_return(
                    value, [jmctl.EXPORT_KINDS[s][1] for s in cfg["exports"]])
                self.events.put(("result", [result]))
            else:
                # Batch API: one failure must not abort the others.
                api = (jmcomic.download_photo if cfg["kind"] == "photo"
                       else jmcomic.download_album)
                value = api(parsed, option, extra=extra)
                payload = jmctl.serialize_download_return(
                    value, [jmctl.EXPORT_KINDS[s][1] for s in cfg["exports"]])
                results = payload.get("results", [])
                for jmid, err in (payload.get("failed") or {}).items():
                    self.events.put(("log", f"[失败] JM{jmid}: {err}"))
                self.events.put(("result", results))

            self.events.put(("done", None))

        except jmcomic.PartialDownloadFailedException as e:
            downloader = e.downloader
            self.events.put(("log", f"[部分失败] {e}"))
            if downloader.download_failed_image:
                self.events.put(("log", f"  失败图片数：{len(downloader.download_failed_image)}"))
            self.events.put(("error", "部分内容下载失败。重新点“开始下载”即可续传（已下载的会跳过）。"))
        except jmcomic.MissingAlbumPhotoException as e:
            self.events.put(("log", f"[错误] 本子/章节不存在：{e.error_jmid}"))
            self.events.put(("error", f"本子/章节不存在：{e.error_jmid}（也可能是该本子需要登录）"))
        except jmcomic.JmcomicException as e:
            self.events.put(("log", f"[错误] jmcomic: {e}"))
            self.events.put(("error", str(e)))
        except jmctl.OperationError as e:
            self.events.put(("log", f"[错误] {e.message}"))
            if e.hint:
                self.events.put(("log", f"       {e.hint}"))
            self.events.put(("error", e.message))
        except Exception as e:
            self.events.put(("log", traceback.format_exc()))
            self.events.put(("error", f"{type(e).__name__}: {e}"))

    def _build_option(self, jmcomic, cfg):
        """Build a JmOption from the GUI fields without writing a temp file."""
        option = jmcomic.JmOption.default()

        base_dir = Path(cfg["save_dir"]).expanduser()
        base_dir.mkdir(parents=True, exist_ok=True)
        option.dir_rule.base_dir = str(base_dir)
        # Group chapters under the album id so multi-chapter albums stay tidy.
        option.dir_rule.rule = "Bd / Aid / Ptitle"

        option.download.threading.image = cfg["threads"]
        option.download.cache = True

        if cfg["proxy"]:
            option.client.postman.meta_data.proxies = {
                "http": cfg["proxy"],
                "https": cfg["proxy"],
            }
        # When the field is empty, leave jmcomic's own default (no proxy) untouched.

        return option

    # -- main-thread event pump --------------------------------------------- #

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()

                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status.set(payload)
                elif kind == "result":
                    for item in payload:
                        self._report_result(item)
                elif kind == "error":
                    self.status.set(f"失败：{payload}")
                    self.progress.stop()
                    self._reset_buttons()
                    messagebox.showerror("下载失败", payload)
                elif kind == "done":
                    self.progress.stop()
                    self._reset_buttons()
                    if self.cancel_flag.is_set():
                        self.status.set("已取消")
                    else:
                        self.status.set("全部完成")
                    self._append_log("===== 结束 =====\n")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_events)

    def _report_result(self, item):
        save_path = item.get("savePath") or ""
        images = item.get("imageCount")
        exports = item.get("exportFiles") or []
        self._append_log(
            f"[完成] JM{item.get('id')} {item.get('title') or ''}\n"
            f"       保存位置：{save_path}\n"
            f"       图片：{images} 张 · 耗时：{item.get('durationSec')} 秒"
        )
        if exports:
            for path in exports:
                self._append_log(f"       导出文件：{path}")
        elif self.export_vars and any(v.get() for v in self.export_vars.values()):
            self._append_log("       提示：已勾选导出但没有产出文件，通常是缺少对应依赖。")

    def _reset_buttons(self):
        self.start_btn.state(["!disabled"])
        self.cancel_btn.state(["disabled"])
        self.worker = None

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("仍在下载", "下载尚未完成，确定要退出吗？"):
                return
            self.cancel_flag.set()
        self.root.destroy()


def _selftest(album_id: str) -> int:
    """
    Non-GUI verification path: prove the frozen bundle can import jmcomic, reach the
    site, and write files. Used by build.py's post-build check.

        jmcomic-downloader.exe --selftest 438696
    """
    import tempfile

    jmctl.route_jm_logs_to_stderr()
    try:
        jmcomic = jmctl.load_jmcomic()
    except jmctl.OperationError as e:
        print(f"FAIL import: {e.message}")
        return 1

    print(f"jmcomic {getattr(jmcomic, '__version__', '?')} imported from {jmcomic.__file__}")

    with tempfile.TemporaryDirectory(prefix="jm-selftest-") as tmp:
        app = DownloaderApp.__new__(DownloaderApp)  # no Tk needed
        cfg = {
            "tokens": [album_id],
            "kind": "photo",
            "exports": ["pdf"],
            "save_dir": tmp,
            "threads": 4,
            "proxy": "",
        }
        option = DownloaderApp._build_option(app, jmcomic, cfg)

        extra = jmcomic.Feature.export_pdf
        try:
            result = jmcomic.download_photo(album_id, option, extra=extra)
        except Exception as e:
            print(f"FAIL download: {type(e).__name__}: {e}")
            return 1

        payload = jmctl.download_result_to_dict(result, ["pdf"])
        print(f"downloaded id={payload['id']} images={payload.get('imageCount')} "
              f"duration={payload['durationSec']}s")
        print(f"savePath={payload['savePath']}")
        for path in payload.get("exportFiles", []):
            print(f"export={path}")
        if not payload.get("imageCount"):
            print("FAIL: no images were written")
            return 1
    print("SELFTEST OK")
    return 0


def main() -> int:
    # jmcomic logs to stdout; keep that off our console and let the GUI own the log view.
    jmctl.route_jm_logs_to_stderr()

    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        try:
            return _selftest(sys.argv[2] if len(sys.argv) > 2 else "438696")
        except Exception:
            detail = traceback.format_exc()
            try:
                # A --windowed build has no console, so surface failures in a dialog
                # instead of exiting silently.
                tk.Tk().withdraw()
                messagebox.showerror("自检失败", detail[-1500:])
            except Exception:
                pass
            print(detail, file=sys.stderr)
            return 1

    root = tk.Tk()
    try:
        # 'vista' gives a native look on Windows; fall back silently elsewhere.
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    DownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
