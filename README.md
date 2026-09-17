# JMComic 下载器 · JMComic_downloader

把 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)（`jmcomic`）包装成四种好用的形态：**Windows / Linux / macOS / Android 图形界面**、**给 AI Agent 用的 skill**、**输出 JSON 的命令行工具**。四端共用同一套下载引擎。

> 本项目只是 `jmcomic` 的外壳（wrapper），下载能力全部来自上游库。请遵守所在地区法律，并尊重目标站点的访问频率限制。

---

## 界面长什么样

程序在本地 `127.0.0.1` 上起一个小服务，用**浏览器**打开界面。没有原生窗口，也不依赖任何 GUI 工具包（不用 Kivy、不用 tkinter）。

```
┌─ JMComic 下载器 ────────────────────────────────┐
│ 车号 / 链接 [ 438696            ]               │
│ 类型        (•) 整本   ( ) 单章                 │
│ 保存到      [C:\Users\你\Downloads\JMComic]     │
│ 导出        [x] PDF  [ ] ZIP  [ ] 长图          │
│ 图片并发    [30]    HTTP 后端 [curl_cffi ▾]     │
│ 代理        [留空 = 跟随系统            ]        │
│ [开始下载] [取消] [清空日志] [退出程序]          │
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░                               │
│ 状态：全部完成                                   │
├─ 日志 ──────────────────────────────────────────┤
│ [完成] JM438696 [MANA] 神里绫华 Another story …  │
│        保存位置：C:\Users\你\Downloads\...       │
│        图片：16 张 · 耗时：9.25 秒               │
│        导出文件：…[JM438696]….pdf                │
└─────────────────────────────────────────────────┘
```

**为什么用浏览器而不是原生窗口？** 一开始用的是 Kivy（原生窗口），但 Kivy 依赖 SDL2 这套 native 图形栈，导致打包出来的程序在无显示环境下直接崩、macOS 上无法构建。换成网页后，程序只需要 Python + jmcomic，**打包几乎不会失败**，四个平台也共用同一套界面代码。桌面端的代价是界面在浏览器标签页里，而不是独立窗口。

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| 按车号下载 | 输入 `438696` / `JM438696` / 完整 18comic 链接都能识别；支持一次填多个 |
| 整本 / 单章 | 「整本」下载全部章节，「单章」只下指定章节 |
| 元数据查询 | 标题、作者、标签、作品、角色、页数、发布时间、观看/点赞/评论数、章节列表 |
| 搜索 | 站内搜索、按标签 / 作品 / 角色搜索，支持分类、副分类、时间范围、排序 |
| 排行榜 | 日榜 / 周榜 / 月榜，以及任意分类组合 |
| 评论 | 本子评论（含回复）和全站最新评论 |
| 导出 | 下载后自动生成 PDF、ZIP、长图 PNG |
| 断点续传 | 已下载的图片自动跳过，重新点一次就能续 |
| 代理 | 支持 HTTP 代理，可在界面里直接填 |

**不包含**：收藏夹、每日签到、收藏夹导出等需要登录的功能。需要时请自行配置 `option.yml` 里的 `login` 插件（见[配置](#配置)）。

---

## 下载与平台支持

到 [Releases](https://github.com/NanNank0/JMComic_downloader/releases/latest) 下载对应平台的包，**都不需要安装 Python**。

| 平台 | 产物 | 状态 |
|---|---|---|
| **Windows** | `jmcomic-windows-x64.zip` | ✅ 本机实测（打包 + 页面 + 真实下载） |
| **Linux** | `jmcomic-linux-x86_64.tar.gz` | ✅ CI 构建 + 冻结产物端到端通过 |
| **macOS** | `jmcomic-macos.zip`（内含 `.app`） | ✅ CI 构建通过（未签名，见下） |
| **Android** | `jmcomicdownloader-*.apk`（约 32 MB） | ✅ CI 构建产出 APK，**未在真机验证** |

四端都由 GitHub Actions 自动构建，打 tag 时会把产物挂到 Release。

> **macOS 未签名**：Gatekeeper 会拦截，用户需先执行
> `xattr -dr com.apple.quarantine JMComic下载器.app`（详见 [PACKAGING.md](PACKAGING.md)）。
>
> **Android APK 尚未在真机运行过**：开发机是 Windows，无法本地构建或安装 APK。
> 构建链路已全部打通并产出可下载的 APK，但"装到手机上能用"这一步需要你实测——
> 验证步骤见 [ANDROID.md](ANDROID.md)。

---

## 三种用法

### 方式一：图形界面（推荐给普通用户）

**Windows**：双击 `jmcomic-downloader.exe`，会自动打开浏览器并显示界面。

**从源码运行**（任何平台）：

```bash
python -m pip install jmcomic
python webui/server.py            # 自动打开浏览器
python webui/server.py --no-browser   # 只起服务，自己打开打印出来的地址
python webui/server.py --port 8765    # 指定端口
python webui/server.py --url-file url.txt   # 把地址写入文件（打包版没控制台时用得上）
```

界面上可以设置：整本 / 单章、保存目录、导出格式（PDF / ZIP / 长图）、图片并发数、HTTP 后端、代理。点「退出程序」会停掉本地服务。

### 方式二：给 AI Agent 用（DSH / Codex / Claude Code）

本项目就是一个标准 skill 目录，能被任何读取 `SKILL.md` 的 agent 加载：

```powershell
# 默认装到 ~/.agents/skills（DSH 和 Codex 都会扫描这个目录）
python scripts/install.py

# 或者装到你需要的所有位置
python scripts/install.py --target agents dsh codex claude
```

装完**新开一个会话**，agent 的技能列表里就会出现 `jmcomic`。之后你可以直接对 agent 说：

> 帮我查一下 JM438696 的信息
> 搜一下 MANA 的无修正本子，下载第一本并导出 PDF

Agent 会自己调用 `scripts/jmctl.py`。想卸载：

```powershell
python scripts/install.py --uninstall --target all
```

### 方式三：命令行 / 自动化

```powershell
python scripts/jmctl.py info 438696                 # 查元数据
python scripts/jmctl.py search "MANA"               # 搜索
python scripts/jmctl.py rank --period week          # 周榜
python scripts/jmctl.py download 438696 --export pdf  # 下载 + 导出 PDF
```

每个命令都**只往 stdout 输出一个 JSON 对象**，日志走 stderr，方便被脚本或程序消费。

---

## 从源码运行

需要 **Python 3.9+**（网页界面没有额外版本要求，Kivy 时代的 3.12 限制已经取消）。

```powershell
python -m pip install jmcomic
python -m pip install img2pdf Pillow        # 可选：PDF / 长图导出

python scripts/jmctl.py doctor              # 检查环境
python webui/server.py                      # 启动界面
```

`doctor` 会打印它使用的 Python 解释器路径。**如果你的电脑上有多个 Python，一定要用这个路径去装依赖**，否则会出现「装了但还是 import 失败」：

```powershell
"C:\打印出来的\python.exe" -m pip install jmcomic
```

---

## 命令行参考

统一入口：`python scripts/jmctl.py <命令> [参数]`

| 命令 | 作用 |
|---|---|
| `doctor` | 检查解释器、jmcomic 版本、可选依赖、网络连通性 |
| `info <车号…>` | 查元数据（`--kind album\|photo`、`--image-urls`） |
| `search <关键词>` | 搜索（`--mode site\|tag\|work\|actor`，见下方筛选参数） |
| `rank` | 排行（`--period day\|week\|month\|custom`） |
| `comments` | 评论（`--id <本子号>` 或 `--forum`） |
| `download <车号…>` | 下载（`--kind album\|photo`、`--export pdf zip png`） |
| `config` | 打印 / 导出默认 `option.yml` 基线（`--write <路径>`） |

搜索与排行的筛选参数：

- `--page` 页码
- `--order-by`：`mr` 最新、`mv` 观看、`mp` 图片数、`tf` 喜欢、`tr` 评分、`md` 评论
- `--time`：`a` 全部、`t` 今日、`w` 本周、`m` 本月
- `--category`：`0` 全部、`doujin`、`single`、`short`、`another`、`hanman`、`meiman`、`doujin_cosplay`、`3D`、`english_site`
- `--sub-category`：仅 `html` 客户端支持，如 `chinese`、`japanese`、`CG`、`cosplay`、`youth`、`3d`
- 关键词支持 `+标签` 强制包含、`-标签` 排除，例如 `"+全彩 +人妻"`

下载专属参数：`--save-dir`、`--threads`（1–50）、`--proxy`、`--http-backend`。

输出约定：成功是 `{"status":"ok", ...}`，失败是 `{"status":"error","error":"...","hint":"..."}`，并且**退出码为 1**，便于脚本判断。

---

## 配置

配置优先级：**命令行参数 / 界面设置 > `option.yml` > jmcomic 默认值**。

把配置传给 CLI：

```powershell
python scripts/jmctl.py info 438696 --option .\option.yml
# 或者设一次环境变量，之后所有命令都生效
$env:JM_OPTION_PATH = "C:\path\to\option.yml"
```

生成一份带全部默认值的基线文件，然后只保留你要改的部分：

```powershell
python scripts/jmctl.py config --write option.yml
```

常用的配置项（完整注释版见 `assets/option.example.yml`）：

```yaml
client:
  impl: api              # api=移动端接口（默认，兼容性好）；html=网页端（搜索筛选更全）
  postman:
    type: curl_cffi      # 桌面默认；Android 自动用 requests（见 ANDROID.md）
    meta_data:
      proxies: system    # system 跟随系统；null 不用；也可写 "127.0.0.1:7890"
      cookies:           # 只有需要登录才能看的本子才要配
        AVS: ${JM_AVS_COOKIE}   # 用环境变量，不要把密码写进文件

download:
  cache: true            # 已存在的图片跳过（续传就靠它）
  image:
    decode: true         # JM 原图是混淆的，true 表示还原
    suffix: null         # 例如 .jpg 可统一转格式
  threading:
    image: 30            # 同时下几张图，网页端一次最多 50，别调更高
    photo: 16            # 同时下几章

dir_rule:
  base_dir: ./downloads  # 保存根目录
  rule: Bd / Aid / Ptitle  # 路径模板：Bd=根目录，A*=本子字段，P*=章节字段
```

> `cookies` 是**按域名区分**的：从 A 域名拿到的 cookie 在 B 域名上无效。这是最常见的「配了 cookie 还是没用」的原因。

需要登录功能（收藏夹、签到）时，在 `option.yml` 里启用上游插件：

```yaml
plugins:
  after_init:
    - plugin: login
      kwargs:
        username: ${JM_USER}
        password: ${JM_PASS}
```

---

## 自己打包

```powershell
# Windows / Linux / macOS
python -m pip install jmcomic pyinstaller
python gui/build.py                 # 本平台
python gui/build.py --verify        # 打包后自动跑自检（推荐）
python gui/build.py --android       # 打印 Android 构建说明

# Android（只能在 Linux/macOS 上跑）
python gui/build_android.py debug   # -> bin/*.apk
```

各平台的细节、AppImage / deb / dmg、签名与公证，见 **[PACKAGING.md](PACKAGING.md)**。
Android 的 recipe 与 `curl_cffi` 绕行方案，见 **[ANDROID.md](ANDROID.md)**。

---

## 安全性说明

本地服务只绑定 `127.0.0.1`，并且每次启动生成一个随机 token：

- 页面本身在**服务端注入** token（不是放在 URL 里），API 请求都带 token；
- 所以**别的程序或你打开的恶意网页**无法通过盲发请求来驱动这个下载器；
- 服务只监听回环地址，不会暴露到局域网。

---

## 项目结构

```
JMComic_downloader/
├── README.md                  # 本文件
├── SKILL.md                   # Agent 读的技能定义
├── ANDROID.md                 # Android 构建 + curl_cffi 绕行说明
├── PACKAGING.md               # 各桌面平台打包说明
├── buildozer.spec             # Android 构建配置
├── recipes/jmcomic/           # 自定义 p4a recipe（--no-deps 安装 jmcomic）
├── scripts/
│   ├── jmcore.py              # 平台无关的下载引擎（四端共用）
│   ├── jmctl.py               # JSON 命令行入口
│   └── install.py             # 把 skill 装到各 agent 的技能目录
├── webui/
│   ├── server.py              # 本地 HTTP 服务 + JSON/SSE API
│   └── ui.py                  # 界面（HTML/JS，内嵌成字符串）
├── gui/
│   ├── build.py               # 桌面打包（PyInstaller）
│   └── build_android.py       # Android 打包辅助（暂存 + buildozer）
├── tests/
│   ├── test_webui.py          # 源码级端到端测试
│   └── test_frozen.py         # 打包产物端到端测试
├── assets/
│   └── option.example.yml     # 带完整注释的配置模板
└── .github/workflows/         # Android / Linux / macOS 自动构建
```

架构上有一处关键设计：**下载引擎（`jmcore.py`）与界面完全分离**。
引擎不依赖任何 GUI 库、也不需要终端，所以 CLI、网页界面、Android APK 能共用它。
网页界面之所以可行，也正因为引擎本身就能在没有界面的环境里跑。

---

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `jmcomic is not importable` | 依赖装到了别的 Python 上。用 `jmctl.py doctor` 打印的解释器路径重装 |
| 双击 exe 没反应 | 浏览器没自动打开。用 `--url-file url.txt` 拿到地址，手动打开 |
| `could not parse a JM id` | 车号不是纯数字。先从文本里把数字提取出来 |
| `本子/章节不存在` | 号码错了，或者该本子需要登录才能看。先确认号码 |
| `networkOk: false` | 换 `--client-impl`（api ↔ html），或换 HTTP 后端 / 配置代理 |
| 下载成功但没有导出文件 | 对应依赖没装（PDF→`img2pdf`，长图→`Pillow`），或该作品没产出图片 |
| `partial download failure` | 重跑同一条命令即可续传，已下载的会跳过 |
| 端口被占用 | 换一个：`python webui/server.py --port 8765` |
| 中文显示成乱码 | 控制台编码问题。加 `$env:PYTHONIOENCODING='utf-8'` |
| macOS 打不开 App | 未签名。执行 `xattr -dr com.apple.quarantine JMComic下载器.app` |

---

## 开源协议与免责声明

本项目以 **MIT** 协议开源。

- 下载能力来自 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)（同样 MIT），本项目与该项目及 JMComic 网站**没有任何隶属关系**。
- 本项目仅供**学习 Python 跨平台开发、打包、以及 Agent Skill 编写**之用。
- 请自行确认在你的司法辖区内使用本工具是合法的；请勿用于传播或商业用途。
- 请勿滥用：默认并发已经比较保守，**不要**把并发调到 50 以上，也不要短时间内批量抓取大量作品。
