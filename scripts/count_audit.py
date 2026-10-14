import re
from collections import Counter
from pathlib import Path

repo = Path(r"C:\Users\PREM KUMAR\Videos\alpha")


def count_py_tests(root):
    files = 0
    defs = 0
    for p in sorted(root.rglob("*.py")):
        if any(part in {".git", "node_modules", ".venv", "dist", "build"} for part in p.parts):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        files += 1
        defs += len(re.findall(r"^\s*def\s+test_\w+\s*\(", txt, re.M))
    return files, defs


def count_js_tests(root):
    files = 0
    defs = 0
    for p in sorted(root.rglob("*.test.mjs")):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        files += 1
        defs += len(re.findall(r"^(async\s+)?\s*test\s*\(", txt, re.M))
    return files, defs


bt = count_py_tests(repo / "backend" / "tests")
ft = count_js_tests(repo / "frontend")
print("backend tests files/def:", bt)
print("frontend test.mjs files/def:", ft)

allp = []
for root in [repo / "backend" / "tests", repo / "frontend"]:
    for p in root.rglob("*"):
        if p.is_file() and (".py" in p.suffixes or p.name.endswith(".test.mjs")):
            allp.append(p)
c = Counter()
for p in allp:
    c[p.read_bytes().hex()] += 1
dups = {k: v for k, v in c.items() if v > 1}
print("exact-md5 duplicate test files:", len(dups))

# weak asserts
weak = []
for p in sorted((repo / "backend" / "tests").rglob("*.py")):
    if any(part in {".git", "node_modules", ".venv", "dist", "build"} for part in p.parts):
        continue
    txt = p.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(txt.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        if re.match(r"assert\s+(True|None|result|instance|not None)\b", s):
            weak.append((p.relative_to(repo), i))
print("weak-assert lines:", len(weak))
for f, i in weak[:40]:
    print(f"{f}:{i}")
