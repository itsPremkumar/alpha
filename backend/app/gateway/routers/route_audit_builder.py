import ast, os, json

base = "backend/app/gateway/routers"
skip = {"__pycache__"}
results = []
for fn in sorted(os.listdir(base)):
    if fn in skip: continue
    if not fn.endswith(".py"): continue
    path = os.path.join(base, fn)
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    decs = [ast.unparse(d) for d in item.decorator_list]
                    routeinfo = None
                    for dec in item.decorator_list:
                        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                            if dec.func.attr in ("get","post","put","patch","delete","options","head"):
                                routeinfo = (dec.func.attr, ast.unparse(dec))
                    if routeinfo:
                        routes.append((item.name, routeinfo[0], routeinfo[1], decs))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Call):
                    pass
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
            pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "router":
                if f.attr in ("get","post","put","patch","delete","options","head"):
                    routes.append(("<module>", f.attr, ast.unparse(node), []))
    results.append((fn, routes))

out = []
for fn, routes in results:
    if routes:
        out.append({"file": fn, "count": len(routes), "routes": [{"name": n, "method": m, "sig": s, "decos": d} for n, m, s, d in routes]})

print(json.dumps(out, indent=1))
