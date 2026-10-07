#!/usr/bin/env python
"""Consolidated route + auth audit for Alpha Gateway API surface.

Scope (per task write-scope): backend/app/gateway/routers/ and
backend/app/gateway/authz + deps.py (+ app.py middleware wiring).
"""
import ast, glob, os, sys

ROOT = "C:/Users/PREM KUMAR/Videos/alpha"
R = os.path.join(ROOT, "backend/app/gateway/routers")

def decos_of(node):
    out = []
    for d in node.decorator_list:
        if isinstance(d, ast.Call):
            fn = d.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("get","post","put","patch","delete","head","options","trace"):
                out.append(fn.attr)
            if isinstance(fn, ast.Name) and fn.id in ("require_auth","require_permission","require_admin_user","require_session_source","require_request_context"):
                out.append("AUTH:"+fn.id)
    return out

print("========== 1. ROUTE INVENTORY (method | handler | auth-decorators) ==========")
missing = []
for f in sorted(glob.glob(R + "/*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = decos_of(n)
            if not decos:
                continue
            # internal helpers only
            if n.name.startswith("_") and not n.name.startswith("test"):
                continue
            print(f"{os.path.basename(f):26s} {','.join(decos):60s} {n.name}")
print()

print("========== 2. MUTATING ROUTES WITHOUT ANY AUTH DECORATOR ==========")
# mutating = POST/PUT/PATCH/DELETE
for f in sorted(glob.glob(R + "/*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decos = decos_of(n)
        methods = [d for d in decos if d in ("post","put","patch","delete")]
        if not methods:
            continue
        if not any("AUTH:" in d for d in decos):
            missing.append((os.path.basename(f), n.name, n.lineno, ",".join(methods)))
for f, name, lineno, methods in missing:
    print(f"  {f}::{lineno}  {name}  [{methods}]")
print(f"  TOTAL: {len(missing)}")
