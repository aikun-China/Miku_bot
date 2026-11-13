"""MikuBot 运行时兼容补丁（由「首次部署.bat」安装到 .venv/Lib/site-packages）。

Pillow 10 移除了若干旧 API，而上游 nonebot-plugin-imageutils 0.1.17 仍在调用，
导致 nonebot-plugin-petpet 因 require() 失败而整块加载不了。

这里在不修改第三方包文件的前提下补回等价实现：
  - Image.ANTIALIAS  → 已移除，等价于 Image.LANCZOS
  - FreeTypeFont.getsize → 已移除，用 getbbox 换算宽高

sitecustomize 由 CPython 的 site 模块在解释器启动时自动导入，
因此对本虚拟环境内的所有进程生效，无需设置 PYTHONPATH。
"""

try:
    from PIL import Image, ImageFont

    # Pillow 10 移除 Image.ANTIALIAS（原等于 Lanczos 重采样）
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.LANCZOS

    # Pillow 10 移除 Image.CUBIC，原等于 Bicubic
    if not hasattr(Image, "CUBIC"):
        Image.CUBIC = Image.BICUBIC

    # Pillow 10 移除 FreeTypeFont.getsize，按 getbbox 换算
    if not hasattr(ImageFont.FreeTypeFont, "getsize"):

        def _getsize(
            self,
            text,
            mode=None,
            direction=None,
            features=None,
            language=None,
            stroke_width=0,
            anchor=None,
        ):
            bbox = self.getbbox(
                text,
                mode=mode,
                direction=direction,
                features=features,
                language=language,
                stroke_width=stroke_width,
                anchor=anchor,
            )
            return (bbox[2] - bbox[0], bbox[3] - bbox[1])

        ImageFont.FreeTypeFont.getsize = _getsize

except Exception:  # pillow 缺失或 API 变化时静默跳过，不影响启动
    pass
