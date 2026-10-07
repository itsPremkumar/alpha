import json, pathlib, tempfile, sys
sys.path.insert(0, "backend/packages/harness/alpha")
from alpha.harness.continual.state import HarnessState
from alpha.harness.continual.snapshots import HarnessSnapshotManager

TMP = pathlib.Path("C:/Users/PREM KUMAR/Videos/alpha/.scratch")
TMP.mkdir(exist_ok=True)
d = TMP / "hl"
d.mkdir(exist_ok=True)
p = d / "harness_state.json"
p.write_text(json.dumps({"entries": {"prompt": [{"id": "a", "kind": "prompt", "title": "x", "content": "y"}]}}), encoding="utf-8")
state = HarnessState(file_path=str(p), scope="local")
sm = HarnessSnapshotManager(state)
sid = sm.create_snapshot("test1")
print("SNAPSHOT create_snapshot id:", sid, "snapshots stored:", sm.list_snapshots())
# corrupt manifest
(m := d / "snapshots" / "manifest.json").write_text("{bad", encoding="utf-8")
print("SNAPSHOT list after corrupt manifest:", sm.list_snapshots())
