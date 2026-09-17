# Linux / macOS / Windows 打包说明

四个平台共用同一套代码：`scripts/jmcore.py`（引擎）+ `gui/kivy_app.py`（Kivy 界面）。
打包只是把这两个文件连同 Python 运行时塞进一个可执行产物。

---

## 通用

所有桌面平台都用 PyInstaller：

```bash
python -m pip install "kivy[base]" jmcomic requests pyinstaller
python gui/build.py                 # 本平台，默认单文件
python gui/build.py --onedir        # 输出文件夹，启动更快
python gui/build.py --console       # 保留控制台，排错用
python gui/build.py --verify        # 打包后自动跑自检
```

`--verify` 会调用产物里的 `--selftest`，真实下载一章来验证 HTTP 和图片解码在冻结环境下仍可用。
**这是唯一能确认打包没坏的方法**，建议每次都加。

⚠️ **Python 版本**：Kivy 目前最高支持到 **3.13**，没有 3.14+ 的 wheel。请用 3.12 或 3.13：

```bash
python3.12 -m venv .venv && . .venv/bin/activate
```

---

## Windows

```powershell
python gui/build.py
# -> dist\jmcomic-downloader.exe
```

单文件约 50–60 MB。首次运行有 SmartScreen 提示（未签名），点「更多信息」→「仍要运行」。

---

## Linux

```powershell
python gui/build.py --onedir
# -> dist/jmcomic-downloader/jmcomic-downloader
```

**必须用 `--onedir`**：单文件模式在 Linux 上启动时要解压到 `/tmp`，如果 `/tmp` 被挂载为 `noexec`
（不少加固过的发行版如此）会直接失败。

### 系统依赖

Kivy 需要 SDL2 和 OpenGL。大多数桌面发行版自带，最小化安装的服务器版需要补：

```bash
# Debian / Ubuntu
sudo apt install -y libgl1 libglib2.0-0 libgstreamer1.0-0 libmtdev1 \
    libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0

# Fedora
sudo dnf install -y mesa-libGL glib2 gstreamer1 mtdev \
    SDL2 SDL2_image SDL2_mixer SDL2_ttf

# Arch
sudo pacman -S --needed mesa glib2 gstreamer mtdev sdl2 sdl2_image sdl2_mixer sdl2_ttf
```

Wayland 下如果窗口异常，强制走 X11：

```bash
SDL_VIDEODRIVER=x11 ./jmcomic-downloader
```

### 打成 AppImage（推荐分发方式）

AppImage 是单文件、免安装、跨发行版。最简单的做法是用 `appimagetool`：

```bash
# 1. 先构建
python gui/build.py --onedir

# 2. 组装 AppDir
mkdir -p AppDir/usr/bin
cp -r dist/jmcomic-downloader/* AppDir/usr/bin/

cat > AppDir/jmcomic-downloader.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=JMComic 下载器
Exec=jmcomic-downloader
Icon=jmcomic-downloader
Categories=Network;FileTransfer;
Terminal=false
EOF

# 3. 生成 AppImage
wget https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage
./appimagetool-x86_64.AppImage AppDir jmcomic-downloader-x86_64.AppImage
```

### 打成 .deb

```bash
sudo apt install -y ruby-dev build-essential
sudo gem install fpm

python gui/build.py --onedir
fpm -s dir -t deb -n jmcomic-downloader -v 1.0.0 \
    --depends libsdl2-2.0-0 --depends libsdl2-image-2.0-0 \
    --depends libsdl2-mixer-2.0-0 --depends libsdl2-ttf-2.0-0 \
    --depends libgl1 \
    dist/jmcomic-downloader/=/opt/jmcomic-downloader/ \
    /dev/null=/dev/null
```

（更省事的办法：直接用 GitHub Actions 的 `linux` job 产物，见下。）

---

## macOS

**必须在 Mac 上构建**——PyInstaller 不能交叉编译。

```bash
python gui/build.py --onedir
# -> dist/jmcomic-downloader.app
```

脚本在 macOS 上会自动改用 `--onedir`，因为 `.app` 本身就是目录结构。

### 两个硬问题

#### 1. Gatekeeper 拦截

未签名的 `.app` 在别人电脑上会被直接拒绝：「无法打开，因为 Apple 无法检查其是否包含恶意软件」。

用户侧的绕过方式（需要你写在下载页上）：

```bash
xattr -dr com.apple.quarantine /Applications/jmcomic-downloader.app
```

或者：右键点 App → **打开** → 再点 **打开**（只需一次）。

#### 2. 架构

- 在 Intel Mac 上构建 → 只能跑 Intel
- 在 Apple Silicon 上构建 → 只能跑 ARM
- 想通吃需要 universal2，代价是体积翻倍：

```bash
python gui/build.py --onedir --target-arch universal2
```

（`build.py` 目前没暴露这个参数，需要的话直接在 `build_args()` 里加
`--target-arch universal2`。注意两个架构的 Kivy 二进制都要装齐。）

### 正式签名与公证（可选，需要 Apple 开发者账号 $99/年）

```bash
# 签名
codesign --deep --force --options runtime \
    --sign "Developer ID Application: Your Name (TEAMID)" \
    dist/jmcomic-downloader.app

# 打包 dmg
hdiutil create -volname "JMComic" -srcfolder dist/jmcomic-downloader.app \
    -ov -format UDZO dist/jmcomic-downloader.dmg

# 公证
xcrun notarytool submit dist/jmcomic-downloader.dmg \
    --apple-id "you@example.com" --team-id TEAMID --password "app-specific-pw" --wait
xcrun stapler staple dist/jmcomic-downloader.dmg
```

没有开发者账号的话，就在 Release 说明里写清楚上面那条 `xattr` 命令。

---

## 用 GitHub Actions 构建（不用买东西/装环境）

`.github/workflows/desktop.yml` 已经配好：

| Job | 产出 | 说明 |
|---|---|---|
| `smoke` | 无 | 字节编译 + 引擎导入检查 + CLI JSON 契约 + Kivy 导入 |
| `linux` | `jmcomic-linux` | Linux 文件夹包，并跑 `--selftest` |
| `macos` | `jmcomic-macos.zip` | `.app` 打包成 zip |

触发方式：打 tag 自动跑，或在 Actions 页面手动 Run workflow。打 tag 时 macOS 产物会自动挂到 Release。

**macOS 产物是未签名的**，下载者需要跑一次 `xattr -dr com.apple.quarantine`。

---

## 验证状态（诚实说明）

开发环境是 Windows，所以各平台实测情况：

| 项目 | 状态 |
|---|---|
| 引擎无 GUI/终端依赖 | ✅ 已实测（CI 里也断言了） |
| Kivy 界面 + 端到端下载 | ✅ 已实测（Windows，含跨线程回调） |
| CLI JSON 契约不变 | ✅ 已实测 |
| **Windows Kivy 打包 + 自检** | ✅ 已实测 |
| **Linux 构建与运行** | ❌ 未实测（无 Linux 环境） |
| **macOS 构建与运行** | ❌ 未实测（无 Mac） |
| **AppImage / deb / dmg** | ❌ 未实测，命令按官方文档写 |

Linux 和 macOS 的构建脚本、CI 配置都是按 PyInstaller 官方用法写的，
但**没有在真实 Linux/Mac 上跑过**。第一次失败的话把 `gui/build.py --console` 的完整输出贴出来。
