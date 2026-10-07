import ast, glob

passed = []
for f in sorted(glob.glob("routers/*.py")):
    src = open(f, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ('get','post','put','patch','delete','options','head') and isinstance(node.func.value, ast.Name) and node.func.value.id == 'router':
                method = node.func.attr
                path = None
                for a in node.args:
                    if isinstance(a, ast.Constant):
                        path = a.value
                        break
                # Find the innermost FunctionDef that contains this call node
                # (strictly: the node is within the body, not the def itself)
                func = None
                candidates = []
                for n in ast.walk(tree):
                    if isinstance(n, ast.FunctionDef) and n.lineno < node.lineno <= (n.end_lineno or 0):
                        candidates.append(n)
                if candidates:
                    candidates.sort(key=lambda x: x.end_lineno - x.lineno)
                    func = candidates[0]
                guards = []
                if func:
                    for dec in func.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                            guards.append(dec.id)
                        elif isinstance(dec, ast.Attribute) and dec.attr in ('require_auth','require_permission','require_admin_user','require_session_user','require_session_source'):
                            guards.append(dec.attr)
                passed.append((f, path, method, func.name if func else '?', guards))

no_guard = [(f,p,m,fn,gs) for (f,p,m,fn,gs) in passed if not any(g in ('require_auth','require_permission','require_admin_user','require_session_source','require_session_user') for g in gs)]
print("ROUTES WITHOUT a require_* guard:")
for f,p,m,fn,gs in no_guard:
    print(f"  {f[-30:]:32} {str(p):44} {m:6} {fn}")
print("count:", len(no_guard))
print("total registered:", len(passed))
