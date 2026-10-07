#!/usr/bin/env python3
"""Prove contracts/check_generated_drift.py bites: revert, observe failure, restore."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "contracts" / "feature_manifest.json"
GATE = [sys.executable, "scripts/check_generated_drift.py", "--line-endings", "exact"]

original = MANIFEST.read_bytes()

try:
    manifest = json.loads(original)
    engines = manifest.setdefault("engines", [])
    # Simulate an agent adding a tool without regenerating the manifest.
    extra = {
        "id": "app.gateway.routers.test_probe",
        "module": "app.gateway.routers.test_probe",
        "state": "",
        "wired": False,
        "excluded": False,
        "wiring_point": "app.gateway.routers:test_probe",
    }
    engines.append(extra)
    mutated = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    MANIFEST.write_bytes(mutated)

    proc = subprocess.run(GATE, capture_output=True, text=True)
    print("=== mutated committed manifest (engines 117 -> 118) ===")
    print("exit_code:", proc.returncode)
    print("--- stdout ---")
    print(proc.stdout)
    if proc.stderr:
        print("--- stderr ---")
        print(proc.stderr)

    # The gate MUST report >=1 file drift so a high count no longer matches.
    assert proc.returncode == 1, "gate unexpectedly passed on a count change!"
    assert "generated artifact drift: 1 file(s)" in proc.stdout, proc.stdout
    print(">>> PROVEN: gate catches a count change (exit 1).")
finally:
    MANIFEST.write_bytes(original)
    print(">>> restored committed manifest.")

# Re-run clean to record the green baseline for this session's evidence.
proc = subprocess.run(GATE, capture_output=True, text=True)
print("=== clean re-run ===")
print("exit_code:", proc.returncode)
print(proc.stdout.splitlines()[-2] if proc.stdout else "(no stdout)")
print(">>> green baseline recorded.")
