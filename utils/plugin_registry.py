"""
插件信息注册中心
所有插件都可以注册自己的信息，供菜单/帮助页面使用
不依赖加载顺序，任何时候都可以注册
支持自定义排序号（order），菜单按 order 升序排列
"""

_PLUGIN_INFO = {}


def register_plugin_info(plugin_name: str, **kwargs):
    """注册插件信息供菜单使用

    参数:
        plugin_name: 插件唯一key
        name: 显示名
        description: 描述
        usage: 使用说明
        commands: 指令列表
        icon: emoji图标
        order: 排序序号（数字越小越靠前，默认99）
    """
    _PLUGIN_INFO[plugin_name] = {
        "name": kwargs.get("name", plugin_name),
        "description": kwargs.get("description", ""),
        "usage": kwargs.get("usage", ""),
        "commands": kwargs.get("commands", []),
        "icon": kwargs.get("icon", "✨"),
        "order": kwargs.get("order", 99),
    }


def get_plugin_info(plugin_name: str):
    """获取插件信息"""
    return _PLUGIN_INFO.get(
        plugin_name,
        {"name": plugin_name, "description": "", "usage": "", "commands": [], "icon": "✨", "order": 99},
    )


def get_all_plugins():
    """获取所有已注册的插件信息（带编号，按 order 升序排列）"""
    sorted_items = sorted(_PLUGIN_INFO.items(), key=lambda x: x[1].get("order", 99))
    result = []
    for i, (key, p) in enumerate(sorted_items, start=1):
        p_copy = dict(p)
        p_copy["index"] = i
        p_copy["key"] = key
        result.append(p_copy)
    return result


def get_plugin_by_index(index: int):
    """按编号查找插件。找不到返回 None。"""
    plugins = get_all_plugins()
    if 1 <= index <= len(plugins):
        return plugins[index - 1]
    return None


def find_plugin(keyword: str):
    """按名称/关键词查找插件"""
    keyword = keyword.strip().lower()
    plugins = get_all_plugins()
    # 先精确匹配编号
    if keyword.isdigit():
        idx = int(keyword)
        p = get_plugin_by_index(idx)
        if p:
            return p
    # 再按名称模糊匹配
    for p in plugins:
        if keyword in p["name"].lower() or keyword in p["key"].lower():
            return p
    # 按指令匹配
    for p in plugins:
        for cmd in p.get("commands", []):
            if keyword in cmd.lower():
                return p
    return None
