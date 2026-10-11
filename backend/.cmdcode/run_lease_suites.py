import os  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402

os.chdir(pathlib.Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

sys.exit(
    pytest.main(
        [
            "-q",
            "-p",
            "no:cacheprovider",
            "backend/tests/test_workflow_leases.py",
            "backend/tests/test_workflow_runtime_correctness.py",
            "backend/tests/test_workflow_event_identity.py",
        ]
    )
)
