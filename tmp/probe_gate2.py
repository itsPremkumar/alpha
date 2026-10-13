import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend")
from support.detectors.blocking_io_runtime import detect_blocking_io_strict, BlockingError

tmp = Path(tempfile.mkdtemp()) / "subdir" / "store.db"
print("db_path:", tmp)
try:
    with detect_blocking_io_strict():
        from alpha.runtime.store._sqlite_utils import ensure_sqlite_parent_dir
        ensure_sqlite_parent_dir(str(tmp))
    print("RESULT: NO_ERROR (gate did NOT raise)  <-- gate broken for this call")
except BlockingError as e:
    print("RESULT: BlockingError raised:", repr(e))
except Exception as e:
    print("RESULT: OTHER exception:", type(e).__name__, e)
