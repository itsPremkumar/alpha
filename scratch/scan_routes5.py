import ast, glob

def get_routes(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    routes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "__init__":
            func_routes = []
            for call in node.body:  # top-level statements in the func
                if isinstance(call, ast.Expr) and isinstance(call.value, ast.Call):
                    cv = call.value
                    if isinstance(cv, ast.Call) and isinstance(cv.func, ast.Attribute):
                        if cv.func.attr in ('get','post','put','patch','delete','options','head') and isinstance(cv.func.value, ast.Name) and cv.func.value.id == 'router':
                            path_arg = None
                            for a in cv.args:
                                if isinstance(a, ast.Constant):
                                    path_arg = a.value
                                    break
                            func_routes.append((cv.func.attr, path_arg, call.lineno))
            if func_routes:
                guards = []
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Name) and dec.id in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                        guards.append(dec.id)
                    elif isinstance(dec, ast.Attribute) and dec.attr in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                        guards.append(dec.attr)
                routes.append((path.split("\\")[-1], node.name, guards, func_routes))
    return routes

all_routes = []
for f in sorted(glob.glob("routers/*.py")):
    for r in get_routes(f):
        all_routes.append((f.split("\\")[-1],) + r)

print(f"{'FILE':24} {'FUNC':28} {'GUARDS':40} ROUTES")
for file, func, guards, routes in all_routes:
    g = ",".join(guards) if guards else "-"
    for m,p,l in routes:
        print(f"{file:24} {func:28} {g:40} {m} {p}  (line {l})")

print("\n=== Routes WITHOUT any require_* guard ===")
missing = [(f,fun,gr,mt,p,l) for (f,fun,gr,route_list) in all_routes for (mt,p,l) in route_list if not gr]
for f,fun,gr,mt,p,l in missing:
    print(f"  {f:24} {fun:28} {gr:40} {mt} {p}  (line {l})")
print("count:", len(missing))
