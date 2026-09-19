# Linux / macOS / Windows 打包说明

四个平台共用同一套代码：`scripts/jmcore.py`（引擎）+ `webui/server.py` + `webui/ui.py`（界面）。
界面是**浏览器里的一个本地页面**，所以打包时不需要任何 GUI 工具包。

---

## 通用

所有桌面平台都用 PyInstaller：

```bash
python -m pip install jmcomic pyinstaller
python gui/build.py                 # 本平台，默认单文件
python gui/build.py --onedir        # 输出文件夹，启动更快
python gui/build.py --console       # 保留控制台（能直接看到界面地址）
python gui/build.py --verify        # 打包后自动跑自检（推荐）
```

`--verify` 会调用产物里的 `--selftest`：真实下载一章并把结果写进
`%TEMP%/jmcomic-downloader-selftest.log`（也会打到 stdout）。**打包版是 `--windowed`，
没有 stdout，所以自检结果一定写文件**——`build.py --verify` 就是读这个文件判断成败的。

因为界面是网页、不依赖任何 GUI 工具包，**没有 Python 版本上限**（3.9+ 都行，本项目在
Python 3.14 上打包验证过）。

---

## Windows

```powershell
python gui/build.py
# -> dist\jmcomic-downloader.exe
```

单文件约 50 MB。首次运行有 SmartScreen 提示（未签名），点「更多信息」→「仍要运行」。

双击后会自动打开浏览器。如果没打开，用 `--url-file` 拿地址：

```powershell
.\dist\jmcomic-downloader.exe --url-file "$env:TEMP\jm-url.txt"
Get-Content "$env:TEMP\jm-url.txt"
```

**建议 `--onedir`**：单文件模式每次启动都要把约 50 MB 解压到临时目录，慢且吃 IO。

---

## Linux

```bash
python gui/build.py --onedir
# -> dist/jmcomic-downloader/jmcomic-downloader
```

**建议用 `--onedir`**：单文件模式在 Linux 上启动时要解压到 `/tmp`，如果 `/tmp` 挂载为
`noexec`（不少加固过的发行版如此）会直接失败。

### 系统依赖

几乎没有。程序不链接 SDL/OpenGL，只需要 Python 运行时和常见的 libc。唯一要注意的是
**打开浏览器**这一步需要 `xdg-open`（一般都自带）：

```bash
sudo apt install -y xdg-utils     # Debian/Ubuntu
sudo dnf install -y xdg-utils     # Fedora
```

如果 `xdg-open` 缺失，程序仍然会启动，只是不会自动开浏览器——用 `--url-file` 或
`--no-browser` 然后自己打开打印出来的地址。

### 打成 AppImage（推荐分发方式）

```bash
python gui/build.py --onedir

mkdir -p AppDir/usr/bin
cp -r dist/jmcomic-downloader/* AppDir/usr/bin/

cat > AppDir/jmcomic-downloader.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=JMComic 下载器
Exec=jmcomic-downloader
Categories=Network;FileTransfer;
Terminal=true
EOF

wget https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x appimagetool-x86_64.AppImage
./appimagetool-x86_64.AppImage AppDir jmcomic-downloader-x86_64.AppImage
```

> `Terminal=true`：这个程序是本地服务，需要一个控制台来显示地址和接收 Ctrl+C。

### 打成 .deb

```bash
sudo apt install -y ruby-dev build-essential
sudo gem install fpm
python gui/build.py --onedir
fpm -s dir -t deb -n jmcomic-downloader -v 1.0.0 \
    --depends xdg-utils \
    dist/jmcomic-downloader/=/opt/jmcomic-downloader/
```

---

## macOS

**必须在 Mac 上构建**——PyInstaller 不能交叉编译。

```bash
python gui/build.py --onedir
# -> dist/jmcomic-downloader.app
```

脚本在 macOS 上会自动用 `--onedir`，因为 `.app` 本身就是目录结构。

### Gatekeeper 拦截

未签名的 `.app` 在别人电脑上会被拒绝打开。用户侧绕过方式（建议写在下载页上）：

```bash
xattr -dr com.apple.quarantine /Applications/jmcomic-downloader.app
```

或者：右键点 App → **打开** → 再点 **打开**（只需一次）。

### 架构

- 在 Intel Mac 上构建 → 只能跑 Intel
- 在 Apple Silicon 上构建 → 只能跑 ARM
- 通吃需要 universal2（体积翻倍），在 `build.py` 的 `build_args()` 里加
  `--target-arch universal2`，并确保两个架构的依赖都装齐。

### 打包 dmg

```bash
hdiutil create -volname "JMComic" -srcfolder dist/jmcomic-downloader.app \
    -ov -format UDZO dist/jmcomic-downloader.dmg
```

### 正式签名与公证（可选，需要 Apple 开发者账号 $99/年）

```bash
codesign --deep --force --options runtime \
    --sign "Developer ID Application: Your Name (TEAMID)" \
    dist/jmcomic-downloader.app

xcrun notarytool submit dist/jmcomic-downloader.dmg \
    --apple-id "you@example.com" --team-id TEAMID --password "app-specific-pw" --wait
xcrun stapler staple dist/jmcomic-downloader.dmg
```

---

## 用 GitHub Actions 构建

`.github/workflows/desktop.yml` 现在覆盖全部四个平台：

| Job | 产出 | 说明 |
|---|---|---|
| `smoke` | 无 | 字节编译 + 引擎无 GUI 依赖断言 + 网页资源检查 + CLI JSON 契约 + **两个 Android 依赖规则测试** |
| `linux` | `jmcomic-linux` / `jmcomic-linux-x86_64.tar.gz` | Linux 文件夹包，并跑**冻结产物**的端到端测试 |
| `windows` | `jmcomic-windows` / `jmcomic-windows-x64.zip` | Windows exe，同样跑冻结产物端到端测试 |
| `macos` | `jmcomic-macos.zip` | `.app` 打包成 zip，同样跑端到端测试 |

打 tag 时四个平台（加上 `.github/workflows/android.yml` 的 APK）都会把产物挂到 Release。

触发方式：打 tag 自动跑，或在 Actions 页面手动 Run workflow。

> 这些 job 只装 Python + PyInstaller，不需要 xvfb，也不需要任何图形库。

---

## 验证状态（诚实说明）

| 项目 | 状态 |
|---|---|
| 引擎无 GUI/终端依赖 | ✅ 已实测（CI 里也断言了） |
| 网页界面：本地服务 + API + SSE | ✅ **已实测**（源码级端到端：16 张图 + PDF） |
| CLI JSON 契约不变 | ✅ 已实测 |
| 缺 curl_cffi / pyyaml / img2pdf 时仍能下载并导出 PDF | ✅ **已实测**（`tests/test_android_optional_deps.py`） |
| **Windows 打包 + 冻结产物端到端** | ✅ **本机实测**（主程序 9.8 MB / onedir 目录约 99 MB / onefile 约 50 MB，Python 3.14） |
| **Linux 构建** | ✅ **CI 全绿**（含冻结产物端到端测试） |
| **Windows 构建** | ✅ **CI 全绿** |
| **macOS 构建** | ✅ **CI 全绿**（产出并校验 `.app`） |
| **Android APK** | ✅ **CI 产出并已真机实测**（约 33 MB，见 ANDROID.md） |
| AppImage / deb / dmg | ❌ 未实测，命令按官方文档写 |

**打包前务必关掉正在运行的旧程序**，否则 Windows 会锁住 `dist` 里的文件，PyInstaller
清理失败会留下**半截的二进制**，运行时报 `Failed to execute script ... unhandled
exception`，看着像代码 bug。
