"""Support modules for ``backend/scripts/`` probe scripts.

These modules are importable both by the probe scripts in this directory
(``sys.path[0]`` is ``backend/scripts`` when a script runs) and by the offline
test suite (``backend`` is on ``sys.path`` via ``tests/conftest.py``, so tests
import them as ``scripts.support.<module>``). Keep them pure: no network, no
runtime state, no imports outside the standard library.
"""
