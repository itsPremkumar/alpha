import re, ast, importlib.util, types, sys

# ---- (A) The $-anchored re.match anti-pattern in skills/validation.py ----
name = "skill\r"
pat = re.compile(r"^[a-z0-9-]+$")
m = pat.match(name)
print("[A] re.match(r'^[a-z0-9-]+$', 'skill\r'): group0 =", m.group(0) if m else None, "bool=", bool(m))
print("[A] re.fullmatch(r'[a-z0-9-]+', 'skill\r'):", re.fullmatch(r"[a-z0-9-]+", name) is not None)
print("[A] re.search(r'^[a-z0-9-]+$', 'skill\r'):", bool(re.search(r"^[a-z0-9-]+$", "skill\r")))

# ---- (B) sandbox/security.py default gates ----
spec = importlib.util.spec_from_file_location(
    "sec", "backend/packages/harness/alpha/sandbox/security.py")
sec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sec)
print("[B] is_host_bash_allowed default:", sec.is_host_bash_allowed())
print("[B] is_in_process_repl_allowed default:", sec.is_in_process_repl_allowed())

# ---- (C) how code_mode reaches the default tool list ----
import glob, os
for f in ["backend/packages/harness/alpha/tools/__init__.py",
          "backend/packages/harness/alpha/tools/builtins/__init__.py",
          "backend/packages/harness/alpha/tools/discovery/__init__.py",
          "backend/packages/harness/alpha/tools/search/catalog.py",
          "backend/packages/harness/alpha/tools/search/tools.py",
          "backend/packages/harness/alpha/tools/tool_discovery_metrics.py"]:
    print("==== ", f)
    txt = open(f, encoding="utf-8", errors="replace").read()
    for i, line in enumerate(txt.splitlines(), 1):
        if "code_mode" in line.lower() or "class CodeMode" in line or "code_mode" in line.lower():
            print(f"  {i}: {line.strip()[:120]}")
