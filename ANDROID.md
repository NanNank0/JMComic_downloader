# Android 构建说明

Android 版用 **Buildozer（python-for-android）** 打包，使用 p4a 的 **`webview` bootstrap**：
Android 侧只是一个装着 WebView 的 Activity，Python 在本地 5000 端口跑我们的网页界面。

**因为界面是网页，Android 版不需要 Kivy，也不需要 SDL2。** 少了一整套 native 图形栈，
构建要处理的原生依赖显著变少。

---

## 一、必须知道的约束：不能用 `curl_cffi`

这是整个 Android 方案的核心难点，改动代码前请先读完这一节。

### 问题

`jmcomic` 的 PyPI 元数据把 `curl-cffi` 列为**硬依赖**：

```
jmcomic -> curl-cffi
```

而 `curl-cffi` 是 **native 扩展**（Rust + CFFI），**python-for-android 没有它的 recipe**（[p4a issue #2964](https://github.com/kivy/python-for-android/issues/2964) 至今未关）。结果是：

```
pip install jmcomic  →  尝试编译 curl-cffi  →  找不到 recipe  →  整个构建失败
```

### 为什么可以绕过

`curl-cffi` 的唯一作用是**伪造浏览器的 TLS 指纹**（`impersonate: chrome`），用来骗过站点的反爬。

**实测结论：JM 的接口不要求 TLS 指纹伪装。** 用纯 Python 的 `requests` 后端完整下载了一整章 16 张图，无 0 字节文件：

```
后端                结果
curl_cffi (默认)     OK
requests             OK     ← 纯 Python
requests 无 impersonate  OK
```

而且 `commonX` 对 `curl_cffi` 是**惰性导入**（只在真正调用时才 `import`），所以只要不选它，根本不会触发。

### 我们的解法

两步配合：

1. **`buildozer.spec` 不直接依赖 jmcomic**，而是手动列出它真正的依赖
   （`commonx`、`pillow`、`pycryptodome`、`pyyaml`、`requests`）——这些都有 recipe 或是纯 Python。
2. **`recipes/jmcomic/__init__.py`** 是我们自己的 recipe，用 `pip install jmcomic --no-deps`
   安装，跳过依赖解析，`curl-cffi` 就永远不会被拉进来。

3. **运行时**：`jmcore.default_http_backend()` 在 Android 上自动返回 `requests`：

```python
def default_http_backend() -> str:
    return ANDROID_HTTP_BACKEND if is_android() else DEFAULT_HTTP_BACKEND
```

### ⚠️ 更正：curl_cffi 并不是惰性导入，已经在模块顶层了

本文档早期版本写过「`curl_cffi` 是惰性导入，只要不选它就不会触发」。**这个判断是错的**，
而且在真机上暴露了出来：装好 APK 后界面能打开，但点下载就报

```
失败：jmcomic is not importable: No module named 'curl_cffi'
```

原因是 `jmcomic/__init__.py` 会 eager 地导入异步客户端模块，而那个模块在**模块顶层**
就 import 了 curl_cffi：

```python
# jmcomic/jm_async_client.py 第 12 行（缩进 0，模块级）
from curl_cffi.requests import AsyncSession

# jmcomic/__init__.py
from .jm_async_client import AsyncJmApiClient   # ← 于是 import jmcomic 必然需要 curl_cffi
```

我当初只检查了 `common/` 包（那里确实是惰性的），漏了 `jm_async_client.py`。

**现在的处理方式**：`jmcore._install_curl_cffi_stub()` 在 curl_cffi 缺失时往
`sys.modules` 注册一个**桩模块**，只提供 `curl_cffi.requests.AsyncSession`，
让 `import jmcomic` 能过。之所以可以这样做，是因为那个符号的唯一使用者是**异步客户端**，
而本应用只用同步 API + `requests` 后端。

桩是「会喊的」而不是「会骗人的」——一旦真被使用就抛明确的错误：

```
RuntimeError: curl_cffi is not available on this platform (python-for-android cannot
build it). This build uses the synchronous jmcomic API with the 'requests' HTTP
backend; the async client is unsupported here.
```

**升级 `jmcomic` 后要验证**（这条约束仍然可能被上游进一步打破——例如别处也加了模块级导入）：

```bash
python tests/test_android_optional_deps.py
```

它会屏蔽 `curl_cffi` / `pyyaml` / `img2pdf` 并模拟 Android 环境，跑一次真实下载 + PDF 导出。
如果上游新增了别的模块级导入，这个测试会立刻失败。

**运行期如何验证**：装好 APK 后跑一次「单章下载」，然后在 logcat 里确认：

```bash
adb logcat -s python:D
```

正常情况下会先看到 `[jmcore] curl_cffi unavailable; installed a stub ...`，
然后下载正常进行。

---

## 二、构建方法

Buildozer **只支持 Linux/macOS，完全不支持 Windows**。你现在在 Windows 上，所以有三个选择。

### 方法 1：WSL2（推荐本地构建）

```powershell
wsl --install -d Ubuntu
```

重启后进入 Ubuntu，然后在项目目录里：

```bash
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip python3-venv \
    autoconf automake libtool pkg-config zlib1g-dev libncurses-dev \
    libtinfo6 cmake libffi-dev libssl-dev build-essential ccache

python3 -m venv .venv && . .venv/bin/activate
pip install buildozer cython==0.29.36
```

然后构建（`gui/build_android.py` 会自动把 `scripts/jmcore.py` 复制进 `gui/`，
构建完再清理掉——因为 `buildozer.spec` 的 `source.dir = gui`，共享引擎必须一起打包进 APK）：

```bash
python gui/build_android.py debug      # -> bin/*.apk
```

产物约 40–60 MB，**首次构建要 30–60 分钟**（要下载 Android SDK/NDK 并编译 Python），之后就快了。

装到手机（手机需开启 USB 调试）：

```bash
buildozer android deploy run logcat
```

### 方法 2：GitHub Actions（不用配环境）

仓库里的 `.github/workflows/android.yml` 会自动构建：

- 推一个 tag（`git tag v1.1.0 && git push origin v1.1.0`）→ 自动构建并把 APK 挂到 Release
- 或在 GitHub 仓库页面 **Actions** → **android** → **Run workflow**

CI 里做了缓存（`~/.buildozer` 和 `.buildozer`），第二次之后构建只要几分钟。

### 方法 3：任何 Linux 机器或虚拟机

命令和方法 1 一样。

---

## 三、签名与发布

Debug APK **能装能用**，但无法上架应用商店。要发布需要签名密钥：

```bash
keytool -genkey -v -keystore release.keystore -alias mykey \
    -keyalg RSA -keysize 2048 -validity 10000
```

把密钥转成 base64 存到 GitHub Secrets（供 CI 使用）：

```bash
base64 -w0 release.keystore    # 复制输出
```

需要的 Secrets：

| Secret | 内容 |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | 上面 base64 的输出 |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 密码 |
| `ANDROID_KEYALIAS` | 别名（如 `mykey`） |
| `ANDROID_KEYALIAS_PASSWORD` | 别名密码 |

然后 Actions → android → Run workflow → 选 `release`。

---

## 四、构建配置要点

`buildozer.spec` 里几处值得注意的设置：

| 配置 | 值 | 原因 |
|---|---|---|
| `source.dir` | `webui` | 只打包网页界面目录，减小 APK |
| `p4a.bootstrap` | `webview` | **关键**：用 WebView 承载界面，因此不需要 Kivy/SDL2 |
| `requirements` | 手动列出 | 见第一节，绕开 curl-cffi；含 `pyjnius`（webview bootstrap 的 Java 层需要） |
| `p4a.local_recipes` | `recipes` | 启用我们的 jmcomic recipe |
| `android.archs` | `arm64-v8a, armeabi-v7a` | 覆盖现代手机与旧设备 |
| `android.minapi` | `24` | Android 7.0+，兼顾覆盖面 |
| `android.wakelock` | `True` | 屏幕熄灭时不让下载中断 |
| `android.permissions` | `INTERNET` + 网络/存储 | 下载和保存文件所需 |

### 端口是写死的 5000

p4a 的 webview bootstrap 会 ping `localhost:5000`，然后加载 `http://127.0.0.1:5000/`
（见 p4a 的 `bootstraps/webview/build/templates/WebViewLoader.tmpl.java`，以及
`bootstraps/common/build/build.py` 里 `--port` 的默认值 `'5000'`）。

所以 `webui/server.py` 在 Android 上会**强制使用 5000 端口**，并且不打开外部浏览器
（界面就在自己的 WebView 里）。如果 5000 被占用会直接报错退出，而不是悄悄换端口——
换端口的话 WebView 就找不到服务了。

### token 为什么放在页面里而不是 URL 里

WebView 加载的是固定地址 `http://127.0.0.1:5000/`，**没有 query string**，所以 token
不能在 URL 里传递。服务端在返回 HTML 时把 token 注入到页面脚本中
（`INDEX_HTML` 里的 `__TOKEN__` 占位符），API 请求仍然带 token。
这样既不破坏 Android 的加载方式，也仍然阻止本机其他程序盲发请求驱动这个下载器。

---

## 五、排错

| 现象 | 原因 / 解决 |
|---|---|
| 构建时报 `curl_cffi` 相关错误 | recipe 没生效。确认 `recipes/jmcomic/__init__.py` 存在，且 `buildozer.spec` 里 `p4a.local_recipes = recipes` |
| `No module named 'jmcore'` | `gui/build_android.py prepare` 没跑，或 `source.dir` 不是 `gui` |
| App 启动即闪退 | 用 `adb logcat -s python:D` 看 Python 报错。最常见是缺依赖或 `jmcore` 没打包进去 |
| 构建卡在下载 SDK/NDK | 正常，首次要很久。CI 里有缓存 |
| `buildozer` 报 Java 版本错 | 需要 **JDK 17**，不是 8 也不是 21 |
| APK 装了但下载失败 | 检查是否走了 `curl_cffi` 后端。网页界面里「HTTP 后端」应显示 `requests` |
| 图片**全部**下载失败，异常是 `cannot identify image file` | Pillow 缺 WebP 编解码器（构建期决定）。见「七、Pillow 的 WebP 编解码器」。`adb logcat -s python:D` 里的 `[jmcomic] Pillow ... webp=NO` 可直接确认 |
| 日志里 50 张图全部 `图片准备下载` 成功、却全部 `图片下载失败` | 同上：HTTP 是成功的，失败在解码。网络/代理/后端都不是原因 |

---

## 六、验证状态（诚实说明）

本项目开发环境是 **Windows**，没有 Linux/macOS，所以：

| 项目 | 状态 |
|---|---|
| `requests` 后端能完整下载 | ✅ **已实测**（Windows，16 张图） |
| 网页界面与控制逻辑 | ✅ **已实测**（本地服务 + API + SSE 端到端） |
| `jmcore` 无 GUI 依赖 | ✅ **已实测** |
| 缺 curl_cffi / pyyaml / img2pdf 仍能下载 | ✅ **已实测**（`tests/test_android_optional_deps.py` 屏蔽三者并模拟 Android，完成真实下载） |
| 缺 img2pdf 仍能导出 PDF | ✅ **已实测**（同上测试，产出 3.9 MB 的有效 PDF） |
| p4a recipe 类 API 与基类匹配 | ✅ **已核对源码**（`PythonRecipe`、`_host_recipe.pip`、`ctx.get_python_install_dir`） |
| p4a `webview` bootstrap 的端口约定 | ✅ **已核对源码**（默认 5000，加载 `http://127.0.0.1:PORT/`） |
| Android SDK / build-tools 就位 | ✅ **CI 已验证**（`build-tools: 34.0.0 37.0.0`） |
| p4a 与新版 pip 不兼容 | ✅ **CI 已验证**（`BuildDependencyInstallError` 消失） |
| 依赖解析（`Auto module resolution`） | ✅ **CI 已验证**（去掉 pyyaml 后通过） |
| APK 内含 Pillow 的 WebP 编解码器 | ✅ **CI 已验证**（`gui/verify_apk.py` 直接读 APK，断言 `PIL/_webp*.so` 存在且所有原生依赖可解析） |
| **APK 构建成功** | ✅ **CI 已产出** |
| **APK 在真机运行** | ⚠️ **部分实测（用户真机 v1.3.7）**：App 启动、内置 WebView 界面、本子信息/章节/图片地址全部正常，50 张图的 HTTP 请求全部成功；但**解码 100% 失败**（缺 WebP 编解码器）。该问题已修，见「七」——**修复后的包仍待真机复测** |

### APK 产出的证据

GitHub Actions 的 `android` workflow 在 tag `v1.3.0` 上是全绿的：

```
[success] Build APK (debug)
[success] Upload APK
[success] Attach APK to the release
```

产物：artifact `jmcomic-apk` **32.06 MB**；Release 附件
`jmcomicdownloader-1.0.0-arm64-v8a_armeabi-v7a-debug.apk` **32.15 MB**。

### 装到手机上怎么验证（需要你做）

APK 是 **debug 签名**，可以直接装：

```bash
adb install -r jmcomicdownloader-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
adb logcat -s python:D          # 看 Python 侧输出
```

装好后应该看到：App 启动 → 内置 WebView 打开界面（本地 5000 端口）→
输入车号 → 开始下载 → 日志区出现进度。

**如果启动即闪退**，看 logcat 里的 Python 报错。最可能的两种情况：

1. **`jmcore` 没打进 APK** —— 确认构建时跑过 `gui/build_android.py prepare`
   （它会把 `scripts/jmcore.py` 暂存进 `webui/`）。CI 里这一步是有的。
2. **WebView 连不上 5000 端口** —— 说明服务没起来。检查 Python 是否报错，
   尤其是 `curl_cffi` 相关（不该出现，因为 Android 会自动用 `requests`）。

**如果下载失败**，在界面里确认「HTTP 后端」显示 `requests`；再不行就配置代理。

### 整条链路踩过并修好的 8 个障碍

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `build-tools folder not found` / `Aidl not found` | buildozer 装的是 2021 年的 cmdline-tools，其 `sdkmanager` 依赖 Java 11 起被移除的 JAXB 类，在 JDK 17 下失效；且 buildozer 在状态匹配时会**跳过** SDK 安装，而 CI 缓存了那个状态 | CI 预装当前版 cmdline-tools，并按 buildozer 期望的旧布局建 `tools/bin/sdkmanager` 链接；构建前删 `.buildozer/state.db` |
| 2 | `ImportError: cannot import name 'BuildDependencyInstallError'` | p4a 从 pip 内部导入已被新版 pip 移除的名字；p4a 又会在自己的 venv 里 `pip install -U pip` | `buildozer.spec` 里 `p4a.branch = develop`。**注意：升级 pip 装的 p4a 没用**，buildozer 跑的是它自己 git clone 的那份 |
| 3 | `Auto module resolution failed` | p4a 对没有 recipe 的包跑 `pip install --only-binary=:all: --platform=android_*`；`pyyaml` 是 C 扩展，72 个 wheel 无一是纯 Python，也没有 `android_*` wheel | 从 requirements 与 recipe depends 中移除 pyyaml（其 import 全是惰性的、且在本 App 不走的路径上） |
| 4 | 构建配置本身 | 界面从 Kivy 换成网页后，Android 侧要用 p4a 的 `webview` bootstrap，而不是 sdl2/kivy | `buildozer.spec` 设 `p4a.bootstrap = webview`，requirements 去掉 kivy、加上 pyjnius |
| 5 | 端口与 token | WebView 加载的是固定地址 `http://127.0.0.1:5000/`，**没有 query string**，token 无法放 URL 里 | Android 上固定用 5000 端口；token 由服务端**注入页面**（`__TOKEN__` 占位符），API 请求仍带 token |
| 6 | **APK 能装能开，但界面一直转圈加载** | `webui/` 里只有 `server.py`，**没有 `main.py`**。p4a 的 webview bootstrap 在**构建时跳过** `main.py` 检查（源码注释原文："webview doesn't need an entrypoint, apparently"），但运行时 `PythonActivity` 仍会启动 `main.py` —— 于是没有任何代码去监听 5000 端口 | 新增 `webui/main.py` 作为 Android 入口，绑定 5000 端口并把启动信息打到 logcat |
| 7 | 界面能开，点下载报 `jmcomic is not importable: No module named 'curl_cffi'` | `jmcomic/jm_async_client.py` 在**模块顶层** `from curl_cffi.requests import AsyncSession`，而 `jmcomic/__init__.py` eager 导入它。curl_cffi 是 Rust 扩展，p4a 无 recipe，Android 上装不了 | `jmcore._install_curl_cffi_stub()` 注册桩模块满足该 import；桩被真正使用时会抛明确错误。详见上文「更正」一节 |
| 8 | 真机上本子信息、章节、50 张图的 HTTP 请求**全部成功**，但 **50 张图全部解码失败**：`cannot identify image file` | Android 的 Pillow 缺 WebP 编解码器：p4a 的 Pillow recipe 把 `libwebp` 放在 `opt_depends`，只有它出现在构建顺序里才会编译 `_webp` 扩展。JM 的图恰好是 `.webp`（详见「七」） | `buildozer.spec` 的 requirements 加上 `libwebp`；并加 `gui/verify_apk.py` 在 CI 上直接读 APK 断言 `PIL/_webp*.so` 存在 |

#### 症状：装了能开，但界面一直加载（第 6 条）

p4a 生成的 Java 侧是**无限重试**，所以服务起不来时界面就永远停在加载页，而不是报错：

```java
// WebViewLoader.java（由 p4a 依据 --port 生成）
public static void testConnection() {
    while (true) {
        if (WebViewLoader.pingHost("localhost", 5000, 100)) {
            PythonActivity.mActivity.loadUrl("http://127.0.0.1:5000/");
            break;
        } else {
            Log.v(TAG, "Could not ping localhost:5000");
            Thread.sleep(100);
        }
    }
}
```

在设备上确认（对着**已经装上的** APK 也能验证这个判断）：

```bash
adb logcat -s WebViewLoader:V python:D
```

- **反复出现** `Could not ping localhost:5000`，且**完全没有** `[jmcomic] main.py starting`
  → 就是本问题：`main.py` 没进 APK 或被启动。
- 出现了 python traceback → `main.py` 跑了但启动失败，按报错修。

`webui/main.py` 会往 logcat 打这些行：

```
[jmcomic] main.py starting
[jmcomic] app dir: /data/user/0/io.github.nannank0.jmcomicdownloader/files/app
[jmcomic] files: ['jmcore.py', 'main.py', 'server.py', 'ui.py']
[jmcomic] android=True backend=requests
[jmcomic] default download dir: /data/user/0/.../files/downloads
```

`files:` 那行特别有用——能直接证明 `jmcore.py` 有没有被暂存进 APK。

第 3 条和第 6 条现在都有测试守着：`tests/test_android_requirements.py` 既复刻 p4a 的 pip 解析，
也检查 `source.dir` 下 `main.py` 是否存在、是否引用 5000 端口。缺文件时报：

```
WEBVIEW ENTRYPOINT: FAIL
  - webui/main.py is missing - the webview bootstrap will build an APK whose WebView
    waits forever on localhost:5000
```

`tests/test_android_optional_deps.py` 更进一步：它设置 p4a 的环境变量让 `is_android()` 为真，屏蔽 `curl_cffi` / `pyyaml` / `img2pdf` 三个模块，然后跑一次**带 PDF 导出的真实下载**。
两个测试都接在普通 CI 的 smoke job 里，几秒钟就能跑完，不需要 Android 工具链。

---

## 七、Pillow 的 WebP 编解码器（v1.3.7 真机「50 张图全部失败」的根因）

### 症状

真机 v1.3.7：车号能正常解析，本子信息、章节、每一张图的下载地址都拿到了，HTTP 全部成功，
但**每一张图**都在保存时失败：

```
图片准备下载: 350234/00001.webp [1/25], [https://cdn-msp3.jmapiproxy2.cc/.../00001.webp] → [...]
图片下载失败: [https://cdn-msp3.jmapiproxy2.cc/media/photos/350234/00017.webp],
异常: [cannot identify image file <_io.BytesIO object at 0x...>]
...
JmDownloader Exit with exception: (<class 'jmcomic.jm_exception.PartialDownloadFailedException'>,
"部分下载失败 共50个图片下载失败: ...")
```

`50/50` 全部失败，而**同一个本子在 Windows 上完全正常**。这条对比就是最大线索：
失败发生在 Pillow 解码那一层，不是网络、代理、后端或 cookie。

### 根因

jmcomic 保存图片走的是
`JmImageResp.transfer_to()` → `JmImageTool.open_image(resp.content)`
→ 解密（把图切成 N 条横向条再重排）→ `img.save(path)`。
JM 的图是 **WebP**，所以整条链路要求 Pillow 具备 WebP 编解码器。

而 Android 上 Pillow 支持哪些格式，是**构建期**决定的。p4a 的 Pillow recipe 把 webp
列为**可选依赖**（源码原文）：

```python
# pythonforandroid/recipes/Pillow/__init__.py
depends = ['png', 'jpeg', 'freetype']
hostpython_prerequisites = ["setuptools>=77"]
opt_depends = ['libwebp']
...
if 'libwebp' in self.ctx.recipe_build_order:
    webp = self.get_recipe('libwebp', self.ctx)
    webp_install = join(webp.get_build_dir(arch.arch), 'installation')
    env["WEBP_ROOT"] = f"{join(webp_install, 'lib')}:{join(webp_install, 'include')}"
```

我们当时没把 `libwebp` 放进 `requirements` → libwebp 不会被构建 → `WEBP_ROOT` 不会设置
→ Pillow 的 `setup.py` 直接跳过 `_webp` 扩展。

### 证据（直接查 v1.3.7 的 APK，不是推测）

```bash
# 1) APK 里根本没有 libwebp.so
unzip -l jm-v137.apk | grep -i webp            # 无输出

# 2) Python 包里有纯 Python 的 PIL/WebPImagePlugin，但没有 _webp 扩展
unzip -p jm-v137.apk lib/arm64-v8a/libpybundle.so | gunzip | tar -t | grep 'PIL/.*\.so'
# _python_bundle/site-packages/PIL/_imaging.so
# _python_bundle/site-packages/PIL/_imagingft.so
# _python_bundle/site-packages/PIL/_imagingmath.so
# ...（没有 _webp.so）
```

`WebPImagePlugin.py` 是纯 Python，永远都在；但它 `from . import _webp`。扩展不存在时
`Image.open()` 对任何 WebP 数据都抛
`UnidentifiedImageError: cannot identify image file`——与真机日志逐字吻合。

### 修法（一行）

```diff
- requirements = python3,pyjnius,requests,commonx,pillow,pycryptodome,jmcomic
+ requirements = python3,libwebp,pyjnius,requests,commonx,pillow,pycryptodome,jmcomic
```

为什么这就够——p4a 的依赖图在展开依赖时，会把 `opt_depends` 里**已经在需求列表中**的项
当成真实依赖边（`pythonforandroid/graph.py`）：

```python
# handle opt_depends: these impose requirements on the build
# order only if already present in the list of recipes to build
dependencies.extend(fix_deplist(
    [[d] for d in recipe.get_opt_depends_in_list(all_inputs)
     if d.lower() not in blacklist]
))
```

所以写上 `libwebp` 会同时得到两件事：它**排在 Pillow 之前**构建，且
`'libwebp' in ctx.recipe_build_order` 为真 → `WEBP_ROOT` 被设置 → Pillow 编译出 `_webp`。

### 一个容易踩的坑：soname

Android 上 CMake 会设置 `CMAKE_PLATFORM_NO_VERSIONED_SONAME`（CMake 的
`Modules/Platform/Android.cmake` 里写着 "Conventionally Android does not use versioned
soname"），所以 `libwebp` 虽然声明了 `SOVERSION 8.0.1`，构建出来仍然是不带版本号的
`libwebp.so`。这点很关键：Android 的 linker 按**精确文件名**匹配 `DT_NEEDED`，而 AGP
只打包以 `.so` 结尾的文件——真要是生成了 `libwebp.so.7`，`PIL/_webp.so` 会在**运行时**
加载失败（构建期毫无提示）。现有 APK 里的 `libjpeg.so` / `libpng16.so` 同样不带版本号，
可作旁证。`gui/verify_apk.py` 会检查每个 `DT_NEEDED` 是否有对应文件，正是为了守住这条。

### 防止复发

- **`gui/verify_apk.py`**（新增）：直接读 APK，对每个 ABI 断言
  1）Python 包里存在 `PIL/_webp*.so`；2）所有原生对象（`lib/<abi>/*.so` 与包内扩展模块）
  的每个 `DT_NEEDED` 都能解析。把 v1.3.7 的包喂给它，它会明确报 FAIL——这正是期望行为。
  本地也能跑：`python gui/verify_apk.py bin/*.apk`。
- **CI**：`android` workflow 在构建之后、上传之前运行它，坏包不会再被发布。
- **`tests/test_android_requirements.py`**：新增 `libwebp` 必须留在 requirements 的检查。
- **真机可查**：启动时 logcat 会多打一行
  `[jmcomic] Pillow 11.3.0 webp=yes jpg=yes zlib=yes ...`，
  以后遇到解码类问题，一行就能定位。

---

## 附录：三个值得记住的内部细节

### 改了 `buildozer.spec` 却没生效？

`p4a create` 会把 spec 里的值（`versionName`、权限、应用名）**烧进生成好的 Android 工程**，
而 buildozer 在 dist 已存在时会**跳过 create**。所以：

- 改 `webui/` 里的**源码** → 会被重新打包，**生效**（`private.tar` 每次重新生成）
- 改 `buildozer.spec` 里的**元数据**（版本、权限、包名）→ 复用旧 dist，**不生效**

真实踩到过：把 `version` 从 `1.0.0` 改成 `1.1.0` 并重新构建后，
APK 文件名和 `AndroidManifest.xml` 里的 `versionName` **仍然是 1.0.0**。

CI 里的应对是**去掉 `.buildozer` 缓存的 `restore-keys` 兜底**：

```yaml
key: buildozer-project-${{ hashFiles('buildozer.spec', 'recipes/**') }}
# 没有 restore-keys，故意不加
```

否则 spec 变了也会命中「近似」的旧缓存。代价是 spec 一改就要全量重建，
但 spec 很少改，正确性优先。本地构建同理：

```bash
rm -rf .buildozer/android/platform/build-*/dists   # 强制重新 create
```

### 怎么确认 APK 里到底有没有你的代码

不需要装到手机上——APK 是个 zip，p4a 把应用源码放在 `assets/private.tar` 里
（`.py` 已被编译成 `.pyc`）：

```python
import zipfile, tarfile, io
z = zipfile.ZipFile('jmcomicdownloader-....apk')
tf = tarfile.open(fileobj=io.BytesIO(z.read('assets/private.tar')))
print([m.name for m in tf.getmembers()])
# ['jmcore.pyc', 'main.pyc', 'server.pyc', 'sitecustomize.pyc', 'ui.pyc', 'p4a_env_vars.txt']
```

用这个办法验证过 `v1.3.2` 的 APK：里面有 `main.pyc`，且其常量池含
`main.py starting` / `ANDROID_WEBVIEW_PORT`，`server.pyc` 含 `__TOKEN__` —— 确认打包的是新代码。

### 为什么升级 pip 的 p4a 没用

buildozer 实际运行的不是 pip 装的那个 p4a，而是它自己 git clone 到
.buildozer/android/platform/python-for-android 的副本（默认 master），
而这个副本会跟着 .buildozer 缓存一起被还原。所以修 p4a 版本要用：

`ini
p4a.branch = develop
`

buildozer 的 _install_p4a() 读 pp.p4a.branch，并在缓存副本的分支与配置不一致时
重新 clone/checkout。（想要完全可复现，可以改用 p4a.commit 固定到某个 sha。）

### 为什么删 buildozer 的状态文件

buildozer 会在「状态看起来没变」时**直接跳过**整个 SDK 安装：

`python
# buildozer/targets/android.py, _install_android_packages()
cache_key = 'android:sdk_installation'
if self.buildozer.state.get(cache_key, None) == cache_value:
    return True          # ← build-tools 永远不会被安装
`

而 CI 缓存了 .buildozer，把「已安装」的状态一起还原了 —— 于是第一次失败之后，
后续每次都在同一处失败。构建前 
m -f .buildozer/state.db 可以强制重新检查。

### 如果以后真的需要在 Android 上用 YAML

两个办法：

1. 写 
ecipes/pyyaml/：PyYAML 的 setup.py 在找不到 libyaml 时会退化为纯 Python 实现；
2. 把调用点换成 
uamel.yaml —— p4a **有** 
uamel.yaml 的 recipe。

改完记得跑 python tests/test_android_requirements.py，它会用 p4a 同样的方式验证解析。

### 想看完整构建日志

本地构建（WSL2）能直接看到全部输出：

`ash
python gui/build_android.py debug
buildozer -v android debug 2>&1 | tail -n 120
`

CI 上失败时，.github/workflows/android.yml 的诊断步骤会把 SDK 布局和 p4a 的真实报错
提取成注解，不需要下载日志。

### PDF 导出为什么不用 img2pdf

`img2pdf` 硬依赖 `pikepdf`（QPDF 的 Python 绑定），而 pikepdf 既没有 android wheel、
p4a 也没有 recipe，所以在 Android 上**根本装不上**（`--only-binary=:all:` 解析直接失败）。

但 `Pillow` 本来就是我们必需的（图片处理），而 Pillow 自己就能写多页 PDF。
于是 `jmcore._export_pdf_with_pillow()` 在下载完成后直接把图片合成 PDF：

```python
first.save(target, "PDF", save_all=True, append_images=rest, resolution=150.0)
```

好处是**一条代码路径四端通用**：桌面端不再需要 img2pdf，Android 也能导出 PDF，
`EXPORT_DEPENDENCIES['pdf']` 也从 `img2pdf` 改成了 `PIL`。

界面上的提示相应改成「PDF / 长图 需 Pillow」。这同时消除了你之前看到的
「缺少导出依赖 img2pdf」提示。

### 未解决（仅影响版本号显示）：APK 的 versionName 一直是 1.0.0

`buildozer.spec` 里写的是 `version = 1.1.0`，`configparser` 读出来也是 `1.1.0`，
但打出来的 APK：

* 文件名是 `jmcomicdownloader-1.0.0-...apk`
* `AndroidManifest.xml` 里 `android:versionName="1.0.0"`

**这只是版本号显示问题，功能不受影响** —— 我用 `assets/private.tar` 验证过，
APK 里的 `main.pyc` / `jmcore.pyc` 都是当前代码（含 curl_cffi 桩与 Pillow PDF 导出）。

**一个错误的诊断，已更正。** 我一度以为这是 CI 缓存污染：第一次构建带着
`restore-keys` 还原了旧 `.buildozer`，又把陈旧状态存到了新 hash 下，导致
`p4a create` 一直被跳过。但把缓存 key 换成全新的 `-v2` 强制干净构建后，
**manifest 依然写 1.0.0** —— 所以这个解释是错的，缓存不是原因。

**已确认的事实：**

* p4a 的模板是 `android:versionName="{{ args.version }}"`，渲染发生在生成 Android
  工程的那一步；
* p4a 的 bootstrap 构建脚本里 `--version` 声明为 `required=True`；
* 但 CI 日志里 buildozer 调用的 `create` 命令**没有** `--version`（有 `--dist_name`、
  `--bootstrap`、`--requirements`、`--copy-libs`、`--ndk-api` 等）；
* buildozer 的 `get_version()` 确实会返回 `app.version`，并且 `--version` 只出现在
  它构造 `apk` 命令的地方。

**没能查清的是**哪一步把 `args.version` 落成了 1.0.0（`required=True` 与实际命令
不含 `--version` 这两点互相矛盾，说明我对命令来源的理解还不完整）。

**可能的绕过**（未验证）：用 buildozer 的透传参数把版本也交给 create：

```ini
p4a.extra_args = --version=1.1.0
```

注意 `p4a.extra_args` 会同时拼到 `create` 和 `apk` 命令上，而 `apk` 那条本身已经带了
`--version`，重复传参的行为需要实测确认。**在验证之前不要把它写进仓库**。

如果哪天需要真正修掉，建议直接看 `buildozer android debug -v` 的完整命令列表，
确认 `create` 到底有没有拿到版本、以及 manifest 是在哪一步被渲染的。

### 已定位：真机 `JavaException: ClassNotFoundException org.kivy.android.PythonActivity`

**客户端完整堆栈（决定性证据）：**

```
server.py:145 _run_job  ->  jmcore.py:688 run_download
  -> jmcomic/api.py:101 download_album -> new_downloader -> create_client
  -> jm_client_impl.py:1289 after_init -> ensure_have_cookies -> get_cookies
  -> setting -> model_data -> res_data -> decoded_data
  -> jm_toolkit.py:1246 decode_resp_data
  -> Crypto/Cipher/AES.py:26  <module>
  -> Crypto/Util/_raw_api.py:77    ImportError: CFFI with optimize=2 fails due to
                                   pycparser bug.
  -> Crypto/Util/_raw_api.py:173   （退回 ctypes 实现）
  -> Lib/ctypes/util.py:11  <module>
  -> android/__init__.py:8  <module>
  -> android/_android.pyx:162
  -> jnius/reflect.py:209 autoclass
  -> JavaException: ClassNotFoundException: Didn't find class
     "org.kivy.android.PythonActivity" on path: DexPathList[[directory "."], ...]
```

**完整因果链：**

1. jmcomic 用 **PyCryptodome 的 AES** 解密 API 响应（`decode_resp_data`）。
2. p4a 上 PyCryptodome 的 CFFI 后端不可用（`CFFI with optimize=2 ...`），于是**退回
   ctypes 实现**，而这个实现会 `import ctypes.util`。
3. p4a **给 CPython 打了补丁**，让 `ctypes/util.py` 一开头就是：

   ```python
   if True:
       from android._ctypes_library_finder import find_library as _find_lib
   elif os.name == "nt":
       ...
   ```

   （见 p4a `recipes/python3/patches/cpython-311-ctypes-find-library.patch`）
   所以**在 Android 上任何 `import ctypes.util` 都会连带导入 `android` 这个 Cython 模块**。
4. `android` 模块经 jnius 调 `autoclass(...)`，而 jnius 取 App 的 ClassLoader 是通过
   `org.kivy.android.PythonActivity`。
5. **我们的下载跑在工作线程里**（`server._run_job` 是 `threading.Thread`）。在非 JVM 创建的
   原生线程上，JNI 的 `FindClass` 用**系统类加载器**，其 dex 路径就是 `directory "."`
   —— 于是找不到 App 里的 `PythonActivity`，抛出上面那个异常。

**这也解释了为什么页面能打开、一点下载才炸**：页面/配置走主线程，JNI 用的是 App 的
类加载器；下载走工作线程，就退化成了系统加载器。

**修法**（`jmcore.ensure_ctypes_util_importable()`，在**主线程**调用）：

1. 先在工作线程启动之前，于**主线程**把 `ctypes.util` 导入一次。Python 会缓存模块，
   工作线程之后 `import ctypes.util` 变成 no-op，不再触发 `android`/jnius。
   调用点在 `webui/main.py` 和 `server.serve()`（都在主线程）。
2. 万一主线程上这个导入也失败，则安装一个**不依赖 JNI** 的
   `android._ctypes_library_finder` 替身（用 stock CPython 在 Android 上的
   `/system/lib{64}/lib*.so` 查找逻辑），并在日志里明确标注这是 workaround。

**验证方式**：启动日志里现在会有

```
[jmcomic] ctypes.util: ctypes.util imported on the calling thread
```

或（走了替身时）

```
[jmcomic] ctypes.util: used a JNI-free ctypes.util shim because the real one failed ...
```

**教训**：我在这条问题上先后给出过两个**错误**的诊断 —— 「CI 缓存污染导致 versionName
不更新」和「`is_android()` 调用了 `platform.platform()`」。两次都是只凭间接证据推断。
真正定位靠的是**拿到完整堆栈**。以后遇到真机问题，先要完整 logcat，再下结论。
（`is_android()` 不再使用 `platform` 模块这件事本身仍然是对的、被测试固定住了，
只是它不是这个异常的原因。）
