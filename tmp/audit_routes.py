import ast, os

BASE = r"C:/Users/PREM KUMAR/Videos/alpha/backend/app/gateway/routers"
GUARD = ("require_auth", "require_permission", "require_admin_user",
         "require_thread_owner", "is_admin_user", "require_session_source",
         "require_graph_execution", "get_current_user", "get_current_active_user")

def cn(v):
    if isinstance(v, ast.Constant):
        return v.value
    if isinstance(v, ast.Name):
        return v.id
    return None

files = sorted(f for f in os.listdir(BASE) if f.endswith(".py"))
mod_prefix = {}
for fn in files:
    try:
        t = ast.parse(open(os.path.join(BASE, fn), encoding="utf-8").read())
    except SyntaxError:
        continue
    for n in t.body:
        if isinstance(n, ast.Assign):
            for tg in n.targets:
                if isinstance(tg, ast.Name) and tg.id == "router":
                    v = n.value
                    if isinstance(v, ast.Call) and cn(v.func) == "APIRouter":
                        p = None
                        for kw in v.keywords:
                            if kw.arg == "prefix":
                                p = cn(kw.value)
                        mod_prefix[fn] = p

routes = []
for fn in files:
    try:
        t = ast.parse(open(os.path.join(BASE, fn), encoding="utf-8").read())
    except SyntaxError:
        continue
    for node in ast.walk(t):
        if isinstance(node, ast.Call):
            f = node.func
            meth = None
            obj = None
            if isinstance(f, ast.Attribute):
                meth = cn(f.attr)
                obj = f.value
            elif isinstance(f, ast.Name):
                meth = cn(f.id)
                obj = f
            if meth in ("get", "post", "put", "patch", "delete", "head", "options"):
                if isinstance(obj, ast.Name) and obj.id in mod_prefix:
                    prefix = mod_prefix[obj.id] or ""
                    path = None
                    for a in node.args:
                        if isinstance(a, ast.Constant):
                            path = a.value
                            break
                    full = (prefix or "") + (path or "")
                    guards = []
                    for item in t.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            for dec in item.decorator_list:
                                if isinstance(dec, ast.Call):
                                    fnc = dec.func
                                    if isinstance(fnc, ast.Attribute) and fnc.attr in GUARD:
                                        guards.append(fnc.attr)
                                    elif isinstance(fnc, ast.Name) and fnc.id in GUARD:
                                        guards.append(fnc.id)
                            for kw in dec.keywords:
                                if kw.arg == "dependencies":
                                    for sub in ast.walk(kw.value):
                                        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "Depends":
                                            guards.append(sub.func.value.id)
                    for item in t.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            for dec in item.decorator_list:
                                if isinstance(dec, ast.Call):
                                    for kw in dec.keywords:
                                        if kw.arg == "dependencies":
                                            for sub in ast.walk(kw.value):
                                                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "Depends":
                                                    guards.append(sub.func.value.id)
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            ff = sub.func
                            if isinstance(ff, ast.Attribute) and ff.attr in GUARD:
                                guards.append(ff.attr)
                    routes.append({"method": meth.upper(), "path": full, "file": fn,
                                   "line": node.lineno, "guards": sorted(set(guards))})

seen = set(); out = []
for r in routes:
    k = (r["method"], r["path"])
    if k in seen: continue
    seen.add(k); out.append(r)
for r in out:
    print(f"{r['method']:6s} {r['path']:70s}  [{r['file'][:34]:34s}:{r['line']}] guards={r['guards']}")
print("TOTAL", len(out))
