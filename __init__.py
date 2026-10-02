"""NoneBot discovery shim for the cloned project.

The upstream checkout keeps the importable plugin package one level below its
repository root and also contains ``setup.py``.  NoneBot scans only the
top-level ``src/plugins`` entries, so this shim exposes the inner plugin while
preventing the repository's packaging files from being treated as plugins.
"""

from .nonebot_plugin_mai_arcade import __plugin_meta__

