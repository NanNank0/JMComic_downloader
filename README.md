# JMComic 下载器 · jmcomic-skill

把 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)（`jmcomic`）包装成**三种好用的形态**：一个能直接双击运行的 Windows 窗口程序、一个给 AI Agent 用的 skill、一个返回 JSON 的命令行工具。三者共用同一套下载引擎和配置。

> 本项目只是 `jmcomic` 的外壳（wrapper），下载能力全部来自上游库。请遵守所在地区法律，并尊重目标站点的访问频率限制。

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

## 三种用法

### 方式一：双击 exe（推荐给普通用户）

从 [Releases](https://github.com/NanNank0/jmcomic-skill/releases/latest) 下载 `jmcomic-downloader.exe`。它是一个独立文件，**不需要装 Python，也不需要装 jmcomic**——运行所需的一切都已经打包进去了。

1. 双击 `jmcomic-downloader.exe`
2. 在「车号 / 链接」里输入号码，例如 `438696`
3. 按 **回车** 或点「开始下载」
4. 下载完成后在「日志」区看到保存路径

首次启动约 3–5 秒（需要把内置的运行环境解压到临时目录），之后正常。

> Windows 首次运行可能弹出 SmartScreen 提示（因为没有代码签名）。点「更多信息」→「仍要运行」。

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

## 安装

### 用 exe

不需要安装。跳到[图形界面说明](#图形界面说明)。

### 从源码运行

需要 **Python 3.9+**。

```powershell
# 1. 装运行时依赖
python -m pip install jmcomic

# 2. 想要导出功能的话，装上对应的包
python -m pip install img2pdf Pillow      # PDF / 长图
# 或者一次装齐官方全家桶
python -m pip install "jmcomic[plugins]"

# 3. 检查环境（会报告解释器路径、jmcomic 版本、缺哪些可选依赖、能否联网）
python scripts/jmctl.py doctor
```

`doctor` 会打印它使用的 Python 解释器路径。**如果你的电脑上有多个 Python，一定要用这个路径去装依赖**，否则会出现「装了但还是 import 失败」：

```powershell
"C:\打印出来的\python.exe" -m pip install jmcomic
```

### 启动图形界面

```powershell
python gui/app.py
```

---

## 图形界面说明

```
┌─ 下载目标 ─────────────────────────────────────────────┐
│ 车号 / 链接：[ 438696            ]                     │
│   多个号码用空格或逗号分隔，也支持 JM123 和 18comic 链接  │
│ 类型：  (•) 整本 (album)   ( ) 单章 (photo)             │
├─ 输出设置 ─────────────────────────────────────────────┤
│ 保存到： [C:\Users\你\Downloads\JMComic ] [浏览…]      │
│ 导出：   [x] PDF  [ ] ZIP  [ ] 长图  （PDF 需 img2pdf…）│
│ 图片并发：[ 30 ]                                        │
│ 代理：   [      ]  留空 = 跟随系统；如 127.0.0.1:7890   │
├────────────────────────────────────────────────────────┤
│ [开始下载] [取消] [打开保存目录]          [清空日志]     │
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░                              │
├─ 日志 ─────────────────────────────────────────────────┤
│ 14:19:04  api.update_domain.success 获取到新的API域名…  │
│ [完成] JM438696 [MANA] 神里绫华 Another story 1–3 …      │
│        保存位置：C:\Users\你\Downloads\JMComic\438696\…  │
│        图片：16 张 · 耗时：9.25 秒                       │
│        导出文件：…[JM438696]….pdf                        │
└────────────────────────────────────────────────────────┘
```

要点：

- **多个车号**：用空格、逗号、分号或换行分隔，例如 `438696, 123456`。批量下载时某一个失败**不会**中断其他任务。
- **保存目录**：默认是 `我的文档\Downloads\JMComic`，子目录按 `车号 / 章节标题` 自动分层。
- **导出**：PDF 需要 `img2pdf`，长图需要 `Pillow`。从源码运行时如果没装这两个包，下载仍然正常，只是勾选的导出不会产出文件——界面会提示。
- **取消**：会等当前正在下载的图片结束后停止，已经下好的部分会被保留。
- **续传**：再次点「开始下载」即可，已存在的图片自动跳过。
- **代理**：留空表示跟随系统代理；填 `127.0.0.1:7890` 这类地址则强制走该代理。

命令行自检（不打开窗口，验证 exe 是否完好）：

```powershell
.\dist\jmcomic-downloader.exe --selftest 438696
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

输出约定：成功是 `{"status":"ok", ...}`，失败是 `{"status":"error","error":"...","hint":"..."}`，并且**退出码为 1**，便于脚本判断。

```powershell
# 例子：搜索后把 id 喂给下载
python scripts/jmctl.py search "無修正" --mode tag --pretty
python scripts/jmctl.py download 1472715 --export pdf zip
```

---

## 配置

配置优先级：**命令行参数 > `option.yml` > jmcomic 默认值**。

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

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `jmcomic is not importable` | 依赖装到了别的 Python 上。用 `jmctl.py doctor` 打印的解释器路径重装 |
| `could not parse a JM id` | 车号不是纯数字。先从文本里把数字提取出来 |
| `本子/章节不存在` | 号码错了，或者该本子需要登录才能看。先确认号码 |
| `networkOk: false` | 换 `--client-impl`（api ↔ html），或配置代理。域名由库自己探测，不要手动写死 |
| 下载成功但没有导出文件 | 对应依赖没装（PDF→`img2pdf`，长图→`Pillow`），或该作品没产出图片 |
| `partial download failure` | 重跑同一条命令即可续传，已下载的会跳过 |
| 中文显示成乱码 | 控制台编码问题。加 `$env:PYTHONIOENCODING='utf-8'`，或直接看 JSON 文件而非控制台 |

---

## 开源协议与免责声明

本项目以 **MIT** 协议开源。

- 下载能力来自 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)（同样 MIT），本项目与该项目及 JMComic 网站**没有任何隶属关系**。
- 本项目仅供**学习 Python 桌面开发、打包、以及 Agent Skill 编写**之用。
- 请自行确认在你的司法辖区内使用本工具是合法的；请勿用于传播或商业用途。
- 请勿滥用：默认并发已经比较保守，**不要**把 `threading.image` 调到 50 以上，也不要短时间内批量抓取大量作品。
