import ast, glob, os

routes = []
for f in sorted(glob.glob("routers/*.py")):
    src = open(f, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            decs = node.decorator_list
            has_auth = False
            auth_decs = []
            for d in decs:
                if isinstance(d, ast.Name) and d.id in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                    has_auth = True
                    auth_decs.append(d.id)
                elif isinstance(d, ast.Attribute) and d.attr in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                    has_auth = True
                    auth_decs.append(d.attr)
            routes.append((f, node.name, has_auth, auth_decs[:3]))

no_guard = [(f,n,ds) for (f,n,has,ds) in routes if not has]
print("ROUTES WITHOUT AUTH GUARD:")
for f,n,ds in no_guard:
    print("  ", f, "->", n, ds)
print("count:", len(no_guard))
print("total routes scanned:", len(routes))
