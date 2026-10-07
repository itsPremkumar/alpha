import ast, glob

routes = []
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
                deps = []
                if node.keywords:
                    for kw in node.keywords:
                        if kw.arg == 'dependencies':
                            for d in kw.value.elts if isinstance(kw.value, ast.List) else []:
                                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'Depends':
                                    deps.append(ast.unparse(d.func.value))
                routes.append((f, path, method, node.lineno, deps))

print(f"{'FILE':35} {'PATH':45} {'METHOD':7} {'LINE':5} {'DEPS'}")
for f,path,method,ln,deps in routes:
    print(f"{f[-28:]:35} {str(path):45} {method:7} {ln:5} {deps}")
