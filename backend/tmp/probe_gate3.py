import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd().parent / "tests"))
sys.path.insert(0, str(Path.cwd().parent))
from support.detectors.blocking_io_runtime import BlockingError, detect_blocking_io_strict

tmp = Path(tempfile.mkdtemp()) / "subdir" / "store.db"
print("db_path:", tmp)
try:
    with detect_blocking_io_strict():
        from alpha.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

        ensure_sqlite_parent_dir(str(tmp))
    print("RESULT: NO_ERROR (gate did NOT raise)")
except BlockingError as e:
    print("RESULT: BlockingError raised:", repr(e))
except Exception as e:
    print("RESULT: OTHER exception:", type(e).__name__, e)
