#!/usr/bin/env python
"""Route audit: print every router path + method + handler name,
and flag handlers with NO auth decorator (require_auth / require_permission)."""

import ast, glob, os

ROOT = "/c/Users/PREM KUMAR/Videos/alpha/backend"
ROUTER_DIR = ROOT + "/app/gateway/routers"

auth_names = {"require_auth", "require_permission"}


def decos_of(node):
    out = []
    has_auth = False
    for d in node.decorator_list:
        if isinstance(d, ast.Call):
            fn = d.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("get", "post", "put", "patch", "delete", "head", "options", "trace"):
                out.append(fn.attr)
            if isinstance(fn, ast.Name) and fn.id in auth_names:
                has_auth = True
            if isinstance(fn, ast.Attribute) and fn.attr in auth_names:
                has_auth = True
    return out, has_auth


print("=== ALL ROUTE HANDLERS, AUTH DECORATORS ===")
missing = []
for f in sorted(glob.glob(ROUTER_DIR + "/*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos, has_auth = decos_of(n)
            if not decos:
                continue
            # Skip internal/private helpers (name starts with _ or is _validate/)
            if n.name.startswith("_"):
                # but _require_diagnostics etc. in some routers are public-ish
                pass
            flag = "" if has_auth else "  *** NO AUTH DECORATOR ***"
            if not has_auth:
                missing.append((os.path.basename(f), n.name, n.lineno))
            print(f"{os.path.basename(f):28s} {n.name:42s} {decos}{flag}")

print("\n=== MISSING AUTH DECORATOR ===")
for f, name, lineno in missing:
    print(f"  {f}::{lineno}  def {name}")
