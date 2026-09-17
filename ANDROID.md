# Android 构建说明

Android 版用 **Kivy + Buildozer（python-for-android）** 打包。这篇文章记录了一个**必须知道的运行时约束**，以及怎么构建。

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
# 在构建环境里（不需要 Android）
python -c "
import jmcomic  # 不应报 curl_cffi 缺失
print('版本', jmcomic.__version__)
print('默认 postman:', jmcomic.JmModuleConfig.DEFAULT_OPTION_DICT['client']['postman']['type'])
"
```

如果报 `ModuleNotFoundError: No module named 'curl_cffi'`，说明上游改了导入位置，此时需要：
给 `curl_cffi` 写一个 p4a recipe（用 `libcurl` + `openssl` recipe，两个都有），或者锁定一个更早的 `jmcomic` 版本。

**构建产物上如何验证**：装好 APK 后跑一次「单章下载」，然后在 logcat 里确认没有 `curl_cffi` 相关报错：

```bash
buildozer android deploy run logcat
# 或者
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
| `source.dir` | `gui` | 只打包 GUI 目录，减小 APK |
| `requirements` | 手动列出 | 见第一节，绕开 curl-cffi |
| `p4a.local_recipes` | `recipes` | 启用我们的 jmcomic recipe |
| `android.archs` | `arm64-v8a, armeabi-v7a` | 覆盖现代手机与旧设备 |
| `android.minapi` | `24` | Android 7.0+，兼顾覆盖面 |
| `android.wakelock` | `True` | 屏幕熄灭时不让下载中断 |
| `android.permissions` | `INTERNET` + 网络/存储 | 下载和保存文件所需 |

---

## 五、排错

| 现象 | 原因 / 解决 |
|---|---|
| 构建时报 `curl_cffi` 相关错误 | recipe 没生效。确认 `recipes/jmcomic/__init__.py` 存在，且 `buildozer.spec` 里 `p4a.local_recipes = recipes` |
| `No module named 'jmcore'` | `gui/build_android.py prepare` 没跑，或 `source.dir` 不是 `gui` |
| App 启动即闪退 | 用 `adb logcat -s python:D` 看 Python 报错。最常见是缺依赖或 `jmcore` 没打包进去 |
| 构建卡在下载 SDK/NDK | 正常，首次要很久。CI 里有缓存 |
| `buildozer` 报 Java 版本错 | 需要 **JDK 17**，不是 8 也不是 21 |
| APK 装了但下载失败 | 检查是否走了 `curl_cffi` 后端。Kivy 界面里「HTTP 后端」应显示 `requests` |

---

## 六、验证状态（诚实说明）

本项目开发环境是 **Windows**，没有 Linux/macOS，所以：

| 项目 | 状态 |
|---|---|
| `requests` 后端能完整下载 | ✅ **已实测**（Windows，16 张图） |
| Kivy 界面与控制逻辑 | ✅ **已实测**（Windows，含端到端下载与跨线程回调） |
| `jmcore` 无 GUI 依赖 | ✅ **已实测** |
| p4a recipe 类 API 与基类匹配 | ✅ **已核对源码**（`PythonRecipe`、`_host_recipe.pip`、`ctx.get_python_install_dir`） |
| **APK 实际构建成功** | ❌ **未实测** —— 无 Linux 环境 |
| **APK 在真机运行** | ❌ **未实测** |

也就是说：**recipe 和配置是按官方源码写的，但没跑过一次真实的 APK 构建**。
第一次构建如果失败，请把 `buildozer` 的完整输出贴出来，尤其是 `.buildozer/android/platform/build-*/build.log` 的尾部。
CI 里已经配置了「失败时打印日志尾部」，所以走 GitHub Actions 最容易定位问题。
