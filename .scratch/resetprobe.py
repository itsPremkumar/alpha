import ast, pathlib

root = pathlib.Path("backend/packages/harness/alpha")
# Focus: memory/, learning/, persistence/ (non-test, non-pycache)
patterns = ("json.load", "json.loads", "toml.load", "yaml.load", "pickle.load")
# Reset-before-parse: a bare assignment of an empty container on a line immediately
# before a load call inside the same function body, where that container is later
# saved back (e.g. self._entries = {}, records = [], data = {})
RESET_KWS = ("= []", "= {}", "entries = []", "self._entries =", "self._cache = []",
             "self._goals = []", "self._data = []", "records = []", "data = [{}]", "items = []", "logs = []")
hits = []
for p in sorted(root.rglob("*.py")):
    if "__pycache__" in p.parts or "/tests/" in p.parts or p.name.startswith("test_"):
        continue
    if not any(str(root / "memory") in p.parts or str(root / "learning") in p.parts or str(root / "persistence") in p.parts for _ in [0]):
        # faster: check parent dirs
        if "memory" not in p.parts and "learning" not in p.parts and "persistence" not in p.parts:
            continue
    try:
        src = p.read_text(encoding="utf-8")
    except Exception:
        continue
    try:
        tree = ast.parse(src)
    except Exception:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            f = child.func
            if not (isinstance(f, ast.Name) and f.id in patterns):
                continue
            prev = []
            for j in range(child.lineno - 2, -1, -1):
                s = src.splitlines()[j].strip()
                if s and not s.startswith("#"):
                    prev.append(s)
                    if len(prev) >= 5:
                        break
            for s in prev:
                if any(k in s for k in RESET_KWS):
                    hits.append((str(p.relative_to(root)), child.lineno, s))
                    break
print("RESET-BEFORE-PARSE HIT COUNT:", len(hits))
for h in hits[:120]:
    print(h)
