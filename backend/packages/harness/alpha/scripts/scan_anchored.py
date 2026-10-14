import ast
import pathlib

roots = [
    "backend/packages/harness/alpha/tools",
    "backend/packages/harness/alpha/sandbox",
    "backend/packages/harness/alpha/security",
    "backend/packages/harness/alpha/safety",
    "backend/packages/harness/alpha/guardrails",
    "backend/packages/harness/alpha/authz",
    "backend/packages/harness/alpha/egress",
    "backend/packages/harness/alpha/policy",
    "backend/packages/harness/alpha/computer_use",
]
hits = []
for base in roots:
    p = pathlib.Path(base)
    for f in p.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("match", "search", "fullmatch"):
                    args_text = []
                    for a in node.args:
                        try:
                            args_text.append(ast.unparse(a))
                        except Exception:
                            args_text.append("?")
                    first = args_text[0] if args_text else ""
                    if first.rstrip().endswith("$"):
                        hits.append((str(f), node.lineno, node.col_offset, "pattern_end_dollar", first[:120]))
                    for kw in node.keywords:
                        if kw.arg == "flags":
                            try:
                                v = ast.unparse(kw.value)
                            except Exception:
                                v = "?"
                            if "MULTILINE" in v:
                                hits.append((str(f), node.lineno, node.col_offset, "MULTILINE_flag", first[:120]))

for h in hits:
    print(f"{h[0]}:{h[1]}:{h[2]} {h[3]} {h[4]}")
print("TOTAL", len(hits))
