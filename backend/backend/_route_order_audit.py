#!/usr/bin/env python
import ast, glob, os

ROOT = "/c/Users/PREM KUMAR/Videos/alpha/backend"
names = ["skills.py", "groups.py", "workflows.py", "swarms.py", "mcp.py", "channels.py", "channel_connections.py", "peer_network.py", "skills_workshop.py", "github_webhooks.py"]
for name in names:
    f = os.path.join(ROOT, "app/gateway/routers", name)
    if not os.path.exists(f):
        continue
    tree = ast.parse(open(f, encoding="utf-8").read())
    print(f"\n===== {name} =====")
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = []
            for d in n.decorator_list:
                if isinstance(d, ast.Call):
                    fn = d.func
                    if isinstance(fn, ast.Attribute) and fn.attr in ("get","post","put","patch","delete","head","options","trace"):
                        decos.append(fn.attr)
            if not decos:
                continue
            path = None
            for d in n.decorator_list:
                if isinstance(d, ast.Call):
                    fn = d.func
                    if isinstance(fn, ast.Attribute) and fn.attr in ("get","post","put","patch","delete"):
                        for a in d.args:
                            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                                path = a.value
            doc = ast.get_docstring(n)
            first_line = doc.split("\n")[0][:90] if doc else ""
            print(f"  {','.join(decos):12s} {path!r:45s} l{n.lineno:4d} {first_line}")
