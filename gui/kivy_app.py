#!/usr/bin/env python3
"""
JMComic Downloader - Kivy UI, shared by Android / Linux / macOS / Windows.

One UI for every platform, because Kivy runs on all of them. The tkinter UI looked
more native on Windows but could never run on Android, so keeping both would mean
maintaining two front ends forever.

Threading model (the part that matters)
--------------------------------------
A download runs on a worker thread so the UI stays responsive. Kivy widgets may
only be touched from the main thread, and - unlike tkinter - Kivy has no thread-safe
queue to poll. So every worker->UI update goes through `Clock.schedule_once`, which
is Kivy's documented way to hop back onto the main thread.

Usage:
    python gui/kivy_app.py                 # run from source
    jmcomic-downloader --selftest 438696   # non-GUI verification (packaged builds)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

# --------------------------------------------------------------------------- #
# Must happen BEFORE `import kivy`.
#
# Kivy parses sys.argv at import time. A frozen PyInstaller build is launched with
# its own switches (`--console`, `--windowed`, ...), and Kivy would try to interpret
# them, print its help text and exit with code 2 - which looks exactly like a broken
# build. KIVY_NO_ARGS tells Kivy to leave argv alone. Our own `--selftest` flag is
# parsed by hand below, so nothing is lost.
# --------------------------------------------------------------------------- #
os.environ.setdefault("KIVY_NO_ARGS", "1")
# Keep Kivy's own chatter out of stdout/stderr; the CLI contract lives on stdout and
# the GUI shows jmcomic's log in-window.
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

# Do NOT override KIVY_HOME. Kivy creates its config directory eagerly at import
# time, and when that path contains characters its (narrow) filesystem layer cannot
# handle - a Windows user profile with a CJK name is enough - Kivy exits silently
# with status 1 and prints nothing at all. Letting Kivy keep its own default avoids
# that failure mode entirely.

# --------------------------------------------------------------------------- #
# import jmcore from the sibling scripts/ directory (also inside a frozen bundle)
# --------------------------------------------------------------------------- #

def _candidate_source_dirs():
    here = Path(__file__).resolve().parent
    dirs = [here, here.parent / "scripts", here.parent]
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        dirs = [meipass, meipass / "scripts", exe_dir, exe_dir / "scripts"] + dirs
    return dirs


for _candidate in _candidate_source_dirs():
    if (_candidate / "jmcore.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

try:
    import jmcore
except ImportError as exc:  # pragma: no cover - packaging guard
    raise SystemExit(
        "jmcore.py not found. Keep kivy_app.py in gui/ beside scripts/jmcore.py, "
        f"or place it next to the executable. Searched: "
        f"{', '.join(str(p) for p in _candidate_source_dirs())} ({exc})"
    ) from exc

# On Android, Kivy needs the window created before heavy imports settle; keep the
# jmcomic import lazy (inside the worker) as jmcore already does.
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton

IS_ANDROID = jmcore.is_android()
IS_DESKTOP = not IS_ANDROID

APP_TITLE = "JMComic 下载器"

EXPORT_LABELS = [("pdf", "PDF"), ("zip", "ZIP"), ("png", "长图")]
KIND_LABELS = [("album", "整本"), ("photo", "单章")]


def open_in_file_manager(path: Path) -> None:
    """Best-effort 'reveal this folder', per platform. Never raises."""
    target = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# widgets
# --------------------------------------------------------------------------- #

class Field(BoxLayout):
    """A labelled row: caption on the left, an arbitrary widget on the right."""

    def __init__(self, caption: str, widget, caption_width=dp(84), **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(40),
                         spacing=dp(6), **kwargs)
        self.add_widget(Label(text=caption, size_hint_x=None, width=caption_width,
                              halign="right", valign="middle", font_size=sp(14)))
        widget.size_hint_x = 1
        self.add_widget(widget)


class DownloaderRoot(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kwargs)
        self.worker = None
        self.cancel_flag = threading.Event()
        self.log_sink_handler = None
        self._build()

    # -- construction ------------------------------------------------------- #

    def _build(self):
        # Scrollable so the form still works on a small phone screen.
        scroll = ScrollView(size_hint=(1, None), height=dp(300) if IS_ANDROID else dp(330))
        form = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                         padding=dp(2))
        form.bind(minimum_height=form.setter("height"))

        form.add_widget(Label(
            text="输入车号后点「开始下载」",
            size_hint_y=None, height=dp(26), font_size=sp(15), bold=True,
        ))

        # ids
        self.ids_input = TextInput(
            hint_text="例如 438696，多个用空格或逗号分隔",
            multiline=False, size_hint_y=None, height=dp(40), font_size=sp(15),
        )
        self.ids_input.bind(on_text_validate=lambda *_: self.start_download())
        form.add_widget(Field("车号 / 链接", self.ids_input))

        # kind
        kind_row = BoxLayout(orientation="horizontal", spacing=dp(6))
        self.kind = "album"
        self.kind_buttons = {}
        for value, label in KIND_LABELS:
            btn = ToggleButton(text=label, group="kind", state="down" if value == "album" else "normal",
                               size_hint_y=None, height=dp(36), font_size=sp(14))
            btn.bind(on_press=lambda _b, v=value: self._set_kind(v))
            self.kind_buttons[value] = btn
            kind_row.add_widget(btn)
        form.add_widget(Field("类型", kind_row))

        # save dir + browse
        dir_row = BoxLayout(orientation="horizontal", spacing=dp(6))
        self.dir_input = TextInput(text=str(jmcore.default_download_dir()), multiline=False,
                                   size_hint_y=None, height=dp(40), font_size=sp(13))
        dir_row.add_widget(self.dir_input)
        if IS_DESKTOP:
            browse = Button(text="浏览", size_hint_x=None, width=dp(64), font_size=sp(13))
            browse.bind(on_release=lambda *_: self._browse())
            dir_row.add_widget(browse)
        form.add_widget(Field("保存到", dir_row))

        # exports
        export_row = BoxLayout(orientation="horizontal", spacing=dp(4))
        self.export_checks = {}
        for suffix, label in EXPORT_LABELS:
            cell = BoxLayout(orientation="horizontal", spacing=dp(2))
            chk = CheckBox(active=(suffix == "pdf"), size_hint_x=None, width=dp(34))
            self.export_checks[suffix] = chk
            cell.add_widget(chk)
            cell.add_widget(Label(text=label, font_size=sp(14), halign="left", valign="middle"))
            export_row.add_widget(cell)
        form.add_widget(Field("导出", export_row))

        # threads
        thread_row = BoxLayout(orientation="horizontal", spacing=dp(6))
        self.thread_slider = Slider(min=1, max=50, value=30, step=1, size_hint_y=None,
                                    height=dp(36))
        self.thread_label = Label(text="30", size_hint_x=None, width=dp(34),
                                  font_size=sp(14), valign="middle")
        self.thread_slider.bind(value=lambda _s, v: setattr(self.thread_label, "text", str(int(v))))
        thread_row.add_widget(self.thread_slider)
        thread_row.add_widget(self.thread_label)
        form.add_widget(Field("图片并发", thread_row))

        # http backend (matters on Android, where curl_cffi is unavailable)
        backend_row = BoxLayout(orientation="horizontal", spacing=dp(6))
        default_backend = jmcore.default_http_backend()
        self.backend_spinner = Spinner(
            text=default_backend,
            values=("curl_cffi", "requests", "curl_cffi_session", "requests-session"),
            size_hint_y=None, height=dp(38), font_size=sp(13),
        )
        backend_row.add_widget(self.backend_spinner)
        form.add_widget(Field("HTTP 后端", backend_row))

        # proxy
        self.proxy_input = TextInput(hint_text="留空 = 跟随系统，如 127.0.0.1:7890",
                                     multiline=False, size_hint_y=None, height=dp(38),
                                     font_size=sp(13))
        form.add_widget(Field("代理", self.proxy_input))

        scroll.add_widget(form)
        self.add_widget(scroll)

        # actions
        actions = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46),
                            spacing=dp(6))
        self.start_btn = Button(text="开始下载", font_size=sp(16), bold=True)
        self.start_btn.bind(on_release=lambda *_: self.start_download())
        self.cancel_btn = Button(text="取消", font_size=sp(14), disabled=True,
                                 size_hint_x=None, width=dp(80))
        self.cancel_btn.bind(on_release=lambda *_: self.cancel())
        actions.add_widget(self.start_btn)
        actions.add_widget(self.cancel_btn)
        if IS_DESKTOP:
            open_btn = Button(text="打开目录", font_size=sp(13), size_hint_x=None, width=dp(96))
            open_btn.bind(on_release=lambda *_: self._open_dir())
            actions.add_widget(open_btn)
        self.add_widget(actions)

        # progress + status
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(14))
        self.add_widget(self.progress)
        self.status = Label(text="就绪", size_hint_y=None, height=dp(24), font_size=sp(13),
                            halign="left", valign="middle")
        self.status.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        self.add_widget(self.status)

        # log
        log_box = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(180),
                            spacing=dp(2))
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(26))
        header.add_widget(Label(text="日志", font_size=sp(13), halign="left", valign="middle"))
        clear = Button(text="清空", size_hint_x=None, width=dp(62), font_size=sp(12))
        clear.bind(on_release=lambda *_: self.clear_log())
        header.add_widget(clear)
        log_box.add_widget(header)

        self.log_scroll = ScrollView()
        self.log_label = Label(text="", size_hint_y=None, font_size=sp(12),
                               halign="left", valign="top", markup=False)
        self.log_label.bind(
            width=lambda w, *_: setattr(w, "text_size", (w.width, None)),
            texture_size=lambda w, *_: setattr(w, "height", w.texture_size[1]),
        )
        self.log_scroll.add_widget(self.log_label)
        log_box.add_widget(self.log_scroll)
        self.add_widget(log_box)

    # -- small helpers ------------------------------------------------------ #

    def _set_kind(self, value):
        self.kind = value

    def _browse(self):
        # Kivy has no native directory chooser; implement a minimal one so the
        # desktop build keeps parity with the old tkinter UI.
        try:
            from kivy.uix.filechooser import FileChooserListView
        except Exception:
            return

        chooser = FileChooserListView(path=str(Path(self.dir_input.text or Path.home())),
                                      dirselect=True)
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        box.add_widget(chooser)
        buttons = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        popup = Popup(title="选择保存目录", content=box, size_hint=(0.9, 0.9))

        def choose(*_):
            if chooser.selection:
                self.dir_input.text = chooser.selection[0]
            popup.dismiss()

        ok_btn = Button(text="确定")
        ok_btn.bind(on_release=choose)
        cancel_btn = Button(text="取消")
        cancel_btn.bind(on_release=lambda *_: popup.dismiss())
        buttons.add_widget(ok_btn)
        buttons.add_widget(cancel_btn)
        box.add_widget(buttons)
        popup.open()

    def _open_dir(self):
        target = Path(self.dir_input.text or jmcore.default_download_dir()).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_in_file_manager(target)

    def _popup(self, title, message):
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        scroll = ScrollView()
        scroll.add_widget(Label(text=message, font_size=sp(13), halign="left", valign="top",
                                size_hint_y=None))
        box.add_widget(scroll)
        close = Button(text="知道了", size_hint_y=None, height=dp(42))
        popup = Popup(title=title, content=box, size_hint=(0.9, 0.7))
        close.bind(on_release=lambda *_: popup.dismiss())
        box.add_widget(close)
        popup.open()

    # -- log ---------------------------------------------------------------- #

    def append_log(self, text: str):
        """Must be called on the main thread."""
        current = self.log_label.text
        lines = (current + "\n" + text).splitlines() if current else text.splitlines()
        self.log_label.text = "\n".join(lines[-400:])
        self.log_scroll.scroll_y = 0

    def clear_log(self):
        self.log_label.text = ""

    # -- download ----------------------------------------------------------- #

    def start_download(self):
        if self.worker and self.worker.is_alive():
            return

        raw = self.ids_input.text or ""
        tokens = jmcore.split_id_text(raw)
        if not tokens:
            self._popup("缺少车号", "请先输入至少一个车号，例如 438696")
            return

        exports = [s for s, chk in self.export_checks.items() if chk.active]
        settings = jmcore.DownloadSettings(
            targets=tokens,
            kind=self.kind,
            exports=exports,
            save_dir=(self.dir_input.text or "").strip() or None,
            threads=int(self.thread_slider.value),
            proxy=(self.proxy_input.text or "").strip(),
            http_backend=self.backend_spinner.text,
            dir_rule="Bd / Aid / Ptitle",
        )

        self.cancel_flag.clear()
        self.start_btn.disabled = True
        self.cancel_btn.disabled = False
        self.progress.value = 0
        self.status.text = "正在准备…"
        self.append_log(f"===== 开始：{len(tokens)} 个目标 · 类型={settings.kind} "
                        f"· 导出={exports or '无'} · 后端={settings.http_backend} =====")

        self.worker = threading.Thread(target=self._worker, args=(settings,), daemon=True)
        self.worker.start()

    def cancel(self):
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self.status.text = "正在取消…（当前图片完成后停止）"
            self.append_log("已请求取消，等待当前任务收尾…")

    def _worker(self, settings):
        """
        Runs off the main thread. Raw tkinter/Kivy widget access here would crash or
        corrupt state, so every UI touch goes through Clock.schedule_once.
        """
        def post(fn, *args):
            Clock.schedule_once(lambda _dt: fn(*args), 0)

        log_handler = None
        try:
            jmcomic = jmcore.load_jmcomic()
            log_handler = jmcore.attach_log_sink(lambda text: post(self.append_log, text))

            if self.cancel_flag.is_set():
                post(self.status.__setattr__, "text", "已取消")
                return

            payload = jmcore.run_download(
                settings, jmcomic=jmcomic,
                progress_sink=lambda text: post(self.append_log, text),
            )

            results = payload.get("results") if "results" in payload else [payload]
            for item in results or []:
                post(self._report_result, item)
            for jmid, err in (payload.get("failed") or {}).items():
                post(self.append_log, f"[失败] JM{jmid}: {err}")

            post(self._finish, "已取消" if self.cancel_flag.is_set() else "全部完成")

        except jmcore.OperationError as e:
            post(self.append_log, f"[错误] {e.message}")
            if e.hint:
                post(self.append_log, f"       {e.hint}")
            post(self._finish, f"失败：{e.message}", e.message)
        except Exception as e:
            detail = traceback.format_exc()
            post(self.append_log, detail)
            post(self._finish, f"失败：{type(e).__name__}: {e}",
                 f"{type(e).__name__}: {e}")
        finally:
            if log_handler is not None:
                jmcore.detach_log_sink(log_handler)

    def _report_result(self, item):
        lines = [f"[完成] JM{item.get('id')} {item.get('title') or ''}",
                 f"       保存位置：{item.get('savePath') or ''}",
                 f"       图片：{item.get('imageCount')} 张 · 耗时：{item.get('durationSec')} 秒"]
        for path in item.get("exportFiles") or []:
            lines.append(f"       导出文件：{path}")
        if not item.get("exportFiles") and any(c.active for c in self.export_checks.values()):
            lines.append("       提示：已勾选导出但无产出文件，通常是缺少对应依赖。")
        self.append_log("\n".join(lines))

    def _finish(self, status_text, error_message=None):
        self.start_btn.disabled = False
        self.cancel_btn.disabled = True
        self.progress.value = 100
        self.status.text = status_text
        self.append_log("===== 结束 =====\n")
        self.worker = None
        if error_message:
            self._popup("下载失败", error_message)

    def on_stop(self):
        """Called by the App on window close; do not leave a download half-managed."""
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()


class DownloaderApp(App):
    title = APP_TITLE

    def build(self):
        if IS_DESKTOP:
            Window.size = (760, 900)
            # Kivy warns unless both minimum dimensions are set.
            Window.minimum_width = 520
            Window.minimum_height = 600
        self.root_widget = DownloaderRoot()
        return self.root_widget

    def on_stop(self):
        root = getattr(self, "root_widget", None)
        if root is not None:
            root.on_stop()


# --------------------------------------------------------------------------- #
# non-GUI verification path (used by build.py and packaged --selftest)
# --------------------------------------------------------------------------- #

def _selftest(album_id: str) -> int:
    """
    Prove the build can import jmcomic, reach the site and write files, without
    opening a window. On Android/desktop packaged builds this is the only way to
    validate the bundle from a script.
    """
    jmcore.route_logs_to_stderr()
    print(f"android={jmcore.is_android()} platform={sys.platform}")
    print(f"http backend={jmcore.default_http_backend()}")

    try:
        jmcomic = jmcore.load_jmcomic()
    except jmcore.OperationError as e:
        print(f"FAIL import: {e.message}")
        return 1
    print(f"jmcomic {getattr(jmcomic, '__version__', '?')} from {jmcomic.__file__}")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="jm-selftest-") as tmp:
        settings = jmcore.DownloadSettings(
            targets=[album_id],
            kind="photo",
            exports=["pdf"],
            save_dir=tmp,
            threads=4,
            http_backend=jmcore.default_http_backend(),
        )
        try:
            payload = jmcore.run_download(settings, jmcomic=jmcomic,
                                          progress_sink=lambda t: print(t, file=sys.stderr))
        except Exception as e:
            print(f"FAIL download: {type(e).__name__}: {e}")
            return 1

        item = payload.get("results", [payload])[0] if "results" in payload else payload
        print(f"downloaded id={item.get('id')} images={item.get('imageCount')} "
              f"duration={item.get('durationSec')}s")
        print(f"savePath={item.get('savePath')}")
        for path in item.get("exportFiles") or []:
            print(f"export={path}")
        if not item.get("imageCount"):
            print("FAIL: no images were written")
            return 1
    print("SELFTEST OK")
    return 0


def main() -> int:
    jmcore.reconfigure_streams_utf8()

    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        try:
            return _selftest(sys.argv[2] if len(sys.argv) > 2 else "438696")
        except Exception:
            detail = traceback.format_exc()
            print(detail, file=sys.stderr)
            if IS_DESKTOP:
                try:
                    root = BoxLayout()
                    app = App()
                    app.root = root
                    Popup(title="自检失败", content=Label(text=detail[-1200:]),
                          size_hint=(0.9, 0.8)).open()
                except Exception:
                    pass
            return 1

    DownloaderApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
