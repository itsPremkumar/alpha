#!/usr/bin/env python
import ast, glob, os

# The repo root in this session is C:/Users/PREM KUMAR/Videos/alpha
ROOT = "/c/Users/PREM KUMAR/Videos/alpha"
for f in sorted(glob.glob(ROOT + "/backend/app/gateway/routers/*.py")):
    tree = ast.parse(open(f, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = []
            for d in n.decorator_list:
                if isinstance(d, ast.Call):
                    fn = d.func
                    if isinstance(fn, ast.Attribute) and fn.attr in ("get","post","put","patch","delete","head","options","trace"):
                        decos.append(fn.attr)
            if decos:
                print(os.path.basename(f).ljust(28), n.name.ljust(42), decos)
