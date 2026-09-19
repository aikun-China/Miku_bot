"""
Miku AI 插件异常类
"""


class AIResultException(Exception):
    """AI没有返回结果"""
    pass


class ImageRecognitionException(Exception):
    """图片识别失败"""
    pass


class ConfigException(Exception):
    """配置错误"""
    pass


__all__ = ["AIResultException", "ImageRecognitionException", "ConfigException"]
