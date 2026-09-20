"""fullmemory backend -- composite DeerMem + mem0oss + wiki vault ($0 LTM).

Drop-in contract: folder name == backend name == ``manager_class: fullmemory``.
"""

from .fullmemory_manager import FullMemoryManager

#: Discovered by the factory's ``_scan_backends`` under ``fullmemory``.
MANAGER_CLASS = FullMemoryManager
