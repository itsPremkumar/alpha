import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("build").resolve()))
import measure_footprint as mf  # noqa: E402

REPO = pathlib.Path(".").resolve()
budget = json.loads(mf.BUDGET_PATH.read_text(encoding="utf-8"))["budgets"]

venv, venv_files = mf.walk_tree(REPO / "build" / "measure" / "prod-venv")
frontend, frontend_files = mf.walk_tree(REPO / "frontend" / ".next" / "standalone")
src = mf.source_checkout_bytes(REPO)
script = (REPO / "installer" / "bootstrap.ps1").stat().st_size
node_dir = pathlib.Path("C:/nvm4w/nodejs")
node = mf.walk_tree(node_dir)[0] if node_dir.is_dir() else 0
uv = pathlib.Path("C:/Users/PREM KUMAR/AppData/Local/hermes/bin/uv.exe").stat().st_size
python = mf.walk_tree(pathlib.Path("C:/Users/PREM KUMAR/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none"))[0]

rows = [
    ("installer_script", mf.to_mib(script), budget["installer_script"]),
    ("python_venv_production", mf.to_mib(venv), None),
    ("uv_binary", mf.to_mib(uv), None),
    ("uv_managed_cpython_312", mf.to_mib(python), None),
    ("node_runtime", mf.to_mib(node), None),
    ("frontend_standalone", mf.to_mib(frontend), None),
    ("source_checkout", mf.to_mib(src), budget["source_checkout"]),
]
print(f"{'component':<28}{'MiB':>12}{'budget':>12}  verdict")
for key, mib, bud in rows:
    verdict = "" if bud is None else ("OK" if mib <= bud else "OVER BUDGET")
    bs = "-" if bud is None else f"{bud:.1f}"
    print(f"{key:<28}{mib:>12.1f}{bs:>12}  {verdict}")

subtotal = mf.to_mib(venv + uv + python + node + frontend + src)
print()
print(f"{'installed_footprint (logical)':<28}{subtotal:>12.1f}{budget['installed_footprint']:>12}  {'OK' if subtotal <= budget['installed_footprint'] else 'OVER BUDGET'}")
print(f"{'installed_footprint (real disk)':<28}{mf.to_mib(venv - 347 + uv + python + node + frontend + src):>12.1f}{budget['installed_footprint']:>12}")
print()
print(f"free disk: {mf.to_mib(mf.free_disk_bytes(REPO)):.0f} MiB")
print(f"venv files: {venv_files}, frontend files: {frontend_files}")
