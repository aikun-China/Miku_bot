# 一键部署包（Windows x64）

把 MikuBot 打成一个**解压即用**的压缩包：内置便携 Python 与 uv，目标机器无需预装 Python、uv 或任何依赖。

## 产物

```
deploy/dist/MikuBot-<version>-portable-win-x64.zip
```

解压后的目录结构：

```
MikuBot-<version>-portable-win-x64/
├─ runtime/python/        便携 CPython（uv 下载的 python-build-standalone）
├─ runtime/uv/uv.exe      uv 可执行文件
├─ 一键启动.bat           日常启动入口
├─ 首次部署.bat           创建 .venv 并安装依赖
├─ 使用说明.txt           给最终用户看的说明
├─ .env.example           环境变量模板
├─ patches/sitecustomize.py  Pillow 10 兼容补丁（部署时装入 .venv）
└─ <仓库已跟踪的源码>      bot.py、plugins/、utils/、admin_web/ 等
```

包内所有调用都指向 `runtime/` 下的两个运行时，不读取系统 `PATH` 里的 Python 或 uv。

## 构建

```powershell
pwsh -File deploy/build_portable.ps1
# 可选：指定版本 / 只出目录不压缩
pwsh -File deploy/build_portable.ps1 -UvVersion 0.11.23 -PythonVersion 3.12 -NoZip
```

构建脚本做的事：

1. 下载指定版本的 `uv.exe` 到 `deploy/.cache/`（幂等，已存在则复用）
2. 用该 uv 下载便携 CPython 到 `deploy/.cache/pythons/`
3. 按 `git ls-files` 白名单复制仓库源码（排除 `deploy/` 自身）
4. 覆盖 `deploy/package/` 里的部署骨架
5. 把便携 Python 与 uv 放进 `runtime/`
6. 用 `ZipFile` 最优压缩打包成 zip

依赖被缓存到 `deploy/.cache/`，重复构建不会重新下载。

## 首次部署脚本做了什么

`首次部署.bat` 按顺序执行：

1. 若缺少 `.env`，从 `.env.example` 复制一份
2. `runtime\uv\uv.exe venv .venv --python runtime\python\python.exe --seed`
   —— 用**包内**便携 Python 创建虚拟环境，`--seed` 是为了装上 `pip.exe`
   （`utils/deps_config.py` 与 `utils/check_deps.py` 依赖 `.venv\Scripts\pip.exe`）
3. `runtime\uv\uv.exe sync --frozen --no-install-project --python runtime\python\python.exe`
   —— 按 `uv.lock` 安装锁定版本；锁定记录里所有包都指向腾讯云镜像
   （失败时回退到不带 `--frozen` 的重新解析）
4. 把 `patches\sitecustomize.py` 复制到 `.venv\Lib\site-packages\`，补回 Pillow 10 移除的 API

实测：干净目录下首次部署装 77 个包，约 42 秒（腾讯云镜像）。

### 为什么需要 sitecustomize.py

上游 `nonebot-plugin-imageutils 0.1.17` 仍在调用 Pillow 10 已删除的 `Image.ANTIALIAS`
和 `FreeTypeFont.getsize`，导致 `nonebot-plugin-petpet` 的 `require()` 直接失败、表情包插件整块加载不了。

`patches/sitecustomize.py` 在解释器启动时补回这两个 API：

- `Image.ANTIALIAS` → `Image.LANCZOS`
- `FreeTypeFont.getsize` → 用 `getbbox` 换算宽高

放在 `.venv/Lib/site-packages` 是因为 CPython 的 `site` 模块会自动导入同目录下的 `sitecustomize`，
对虚拟环境内所有进程生效，不必设置 `PYTHONPATH`。已确认 `uv sync` 不会删除该文件。


## 为什么不把 .venv 一起打包

venv 的 `pyvenv.cfg` 里 `home = <绝对路径>`，一旦文件夹被移动或换机器解压到别的路径，venv 立刻失效。
所以 `.venv` 在**目标机器首次部署时**现建，`pyvenv.cfg` 里的路径天然正确。

`一键启动.bat` 会读 `pyvenv.cfg` 校验 `home` 是否仍指向本包的 `runtime\python`，
不匹配就自动重跑首次部署。因此移动文件夹后直接双击启动即可。

## 已知限制

- **仅 Windows x64**。构建脚本与启动脚本都按 `x86_64-pc-windows-msvc` 与 `.bat` 设计。
- **首次部署需要联网**。之后离线可运行。
- **不含浏览器**。`utils/check_deps.py` 的截图策略是调用系统已安装的 Microsoft Edge（`channel="msedge"`），不下载 Chromium。实测启动日志会打印「系统 Edge 已就绪」。
- **表情包资源首次需联网**。`nonebot-plugin-petpet` 的表情素材不在包内，首次加载会按 `petpet_resource_url` 逐文件下载。当前该地址（jsdelivr）对直连返回 301，会反复重试并拖慢首次启动。开发机同样如此，是上游问题，与打包方式无关。
- **不含 site-packages 行为修改**。开发机对 `petpet` 的 `depends.py`/`functions.py`/`manager.py` 还有行为级改动（见 `CHANGELOG.MD`），部署包只包含 Pillow 兼容层，不包含这三处改动，因此安装的是上游原版实现。
- **uv 版本固定**。`build_portable.ps1` 默认 `0.11.23`，与开发环境一致以保证 `uv.lock` 可复现。

## 发布

zip 约 70–90 MB，超过普通提交的合理体积，因此只把本目录的脚本提交进仓库，zip 作为 GitHub Release 附件发布：

```powershell
pwsh -File deploy/build_portable.ps1
gh release create v<version> "deploy/dist/MikuBot-<version>-portable-win-x64.zip" `
    --repo aikun-China/Miku_bot --title "MikuBot <version> 一键部署包" --notes "解压后双击「首次部署.bat」"
```
