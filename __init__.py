"""Amia-plugin-mai-arcade 的 NoneBot 根入口。

仓库目录本身就是插件目录；实际注册 matcher 和服务的实现放在同级
``plugin.py``，避免 pytest 把带连字符的仓库目录误当成可导入的 Python
包，同时不再引入任何内层 ``nonebot_plugin_mai_arcade/`` 目录。
"""

if __package__:
    from .plugin import __plugin_meta__
    from .plugin import *  # noqa: F401,F403
