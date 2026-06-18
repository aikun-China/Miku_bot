"""
MikuBot HTML 模板渲染工具

每个插件现在把自己的模板文件放在插件自身的 templates/ 目录下，
调用时传入自定义路径即可：

    from utils.html_render import render_template
    from utils.screenshot import screenshot_html
    from pathlib import Path

    TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
    html = render_template("ping", TEMPLATES_DIR, nickname="小明", ...)
    img = await screenshot_html(html, width=600, height=350)
    await msg.finish(MessageSegment.image(str(img)))

为了兼容旧调用方式（render_template("ping", ...)），不传第二个参数时
默认使用 utils/templates/。
"""

from pathlib import Path
from typing import Optional
import re

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def render_template(
    template_name: str,
    templates_dir: Optional[Path] = None,
    **kwargs
) -> str:
    """读取模板文件，用 kwargs 替换占位符，返回完整 HTML 字符串。

    参数：
        template_name: 模板文件名（不含 .html 后缀）
        templates_dir: 模板目录，不传则使用 utils/templates/（兼容旧调用）
        **kwargs: 要替换的变量
    """
    if templates_dir is None:
        templates_dir = DEFAULT_TEMPLATES_DIR
    template_path = templates_dir / f"{template_name}.html"
    if not template_path.exists():
        raise FileNotFoundError(f"模板不存在：{template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    # 支持两种占位符格式：{varname} 和 ${varname}
    # 注意：CSS/JS 的 { ... } 里面有空格、冒号等特殊字符，不会被误匹配
    def _replace(match):
        key = match.group(1) or match.group(2)
        return str(kwargs.get(key, match.group(0)))

    # {varname}  或  ${varname}
    result = re.sub(
        r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}|\{([a-zA-Z_][a-zA-Z0-9_]*)\}",
        _replace,
        template_str,
    )
    return result
