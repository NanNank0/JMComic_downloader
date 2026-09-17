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

### ⚠️ 这条约束会不会被上游打破

**会，而且不会有任何提示。** 触发条件是：**未来的 `jmcomic` 版本把 `curl_cffi` 的导入从函数内部移到模块顶层**。那样 `import jmcomic` 就会直接失败。

每次升级 `jmcomic` 后，请验证这一点：

```bash
python -c "
import jmcomic  # 不应报 curl_cffi 缺失
print('版本', jmcomic.__version__)
print('默认 postman:', jmcomic.JmModuleConfig.DEFAULT_OPTION_DICT['client']['postman']['type'])
"
```

如果报 `ModuleNotFoundError: No module named 'curl_cffi'`，说明上游改了导入位置，此时需要：
给 `curl_cffi` 写一个 p4a recipe（用 `libcurl` + `openssl` recipe，两个都有），或者锁定一个更早的 `jmcomic` 版本。

**运行期如何验证**：装好 APK 后跑一次「单章下载」，然后在 logcat 里确认没有 `curl_cffi` 相关报错：

```bash
adb logcat -s python:D
```

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

---

## 六、验证状态（诚实说明）

本项目开发环境是 **Windows**，没有 Linux/macOS，所以：

| 项目 | 状态 |
|---|---|
| `requests` 后端能完整下载 | ✅ **已实测**（Windows，16 张图） |
| 网页界面与控制逻辑 | ✅ **已实测**（本地服务 + API + SSE 端到端） |
| `jmcore` 无 GUI 依赖 | ✅ **已实测** |
| 省略 pyyaml 后仍能正常下载 | ✅ **已实测**（`tests/test_no_yaml.py` 屏蔽 yaml 后完成真实下载） |
| p4a recipe 类 API 与基类匹配 | ✅ **已核对源码**（`PythonRecipe`、`_host_recipe.pip`、`ctx.get_python_install_dir`） |
| p4a `webview` bootstrap 的端口约定 | ✅ **已核对源码**（默认 5000，加载 `http://127.0.0.1:PORT/`） |
| Android SDK / build-tools 就位 | ✅ **CI 已验证**（`build-tools: 34.0.0 37.0.0`） |
| p4a 与新版 pip 不兼容 | ✅ **CI 已验证**（`BuildDependencyInstallError` 消失） |
| 依赖解析（`Auto module resolution`） | ✅ **CI 已验证**（去掉 pyyaml 后通过） |
| **APK 构建成功** | ✅ **CI 已产出** |
| **APK 在真机运行** | ❌ **未实测** —— 开发机为 Windows，无法安装验证 |

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

### 整条链路踩过并修好的 6 个障碍

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `build-tools folder not found` / `Aidl not found` | buildozer 装的是 2021 年的 cmdline-tools，其 `sdkmanager` 依赖 Java 11 起被移除的 JAXB 类，在 JDK 17 下失效；且 buildozer 在状态匹配时会**跳过** SDK 安装，而 CI 缓存了那个状态 | CI 预装当前版 cmdline-tools，并按 buildozer 期望的旧布局建 `tools/bin/sdkmanager` 链接；构建前删 `.buildozer/state.db` |
| 2 | `ImportError: cannot import name 'BuildDependencyInstallError'` | p4a 从 pip 内部导入已被新版 pip 移除的名字；p4a 又会在自己的 venv 里 `pip install -U pip` | `buildozer.spec` 里 `p4a.branch = develop`。**注意：升级 pip 装的 p4a 没用**，buildozer 跑的是它自己 git clone 的那份 |
| 3 | `Auto module resolution failed` | p4a 对没有 recipe 的包跑 `pip install --only-binary=:all: --platform=android_*`；`pyyaml` 是 C 扩展，72 个 wheel 无一是纯 Python，也没有 `android_*` wheel | 从 requirements 与 recipe depends 中移除 pyyaml（其 import 全是惰性的、且在本 App 不走的路径上） |
| 4 | 构建配置本身 | 界面从 Kivy 换成网页后，Android 侧要用 p4a 的 `webview` bootstrap，而不是 sdl2/kivy | `buildozer.spec` 设 `p4a.bootstrap = webview`，requirements 去掉 kivy、加上 pyjnius |
| 5 | 端口与 token | WebView 加载的是固定地址 `http://127.0.0.1:5000/`，**没有 query string**，token 无法放 URL 里 | Android 上固定用 5000 端口；token 由服务端**注入页面**（`__TOKEN__` 占位符），API 请求仍带 token |
| 6 | **APK 能装能开，但界面一直转圈加载** | `webui/` 里只有 `server.py`，**没有 `main.py`**。p4a 的 webview bootstrap 在**构建时跳过** `main.py` 检查（源码注释原文："webview doesn't need an entrypoint, apparently"），但运行时 `PythonActivity` 仍会启动 `main.py` —— 于是没有任何代码去监听 5000 端口 | 新增 `webui/main.py` 作为 Android 入口，绑定 5000 端口并把启动信息打到 logcat |

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

`tests/test_no_yaml.py` 则用 import hook 屏蔽 `yaml` 后跑一次真实下载，证明省略它是安全的。
两个测试都接在普通 CI 的 smoke job 里，几秒钟就能跑完，不需要 Android 工具链。

---

## 附录：两个值得记住的内部细节

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
