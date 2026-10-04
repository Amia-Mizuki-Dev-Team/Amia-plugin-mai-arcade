"""Amia-plugin-mai-arcade 的 NoneBot 根入口。

仓库目录本身就是插件目录；实际注册 matcher 和服务的实现放在同级
``plugin.py``，根目录的 ``__init__.py`` 直接作为 NoneBot 插件入口。
"""

if __package__:
    from .plugin import __plugin_meta__
    from .plugin import *  # noqa: F401,F403
