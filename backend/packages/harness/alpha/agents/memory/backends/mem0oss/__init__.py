"""mem0oss memory backend -- local OSS Mem0 with Ollama (free, no keys).

Drop-in contract: folder name == backend name == ``manager_class: mem0oss``.
Apache-2.0 (mem0ai/mem0). See README.md for the $0 Ollama setup.
"""

from .mem0oss_manager import Mem0OssManager

#: Discovered by the factory's ``_scan_backends`` under ``mem0oss``.
MANAGER_CLASS = Mem0OssManager
