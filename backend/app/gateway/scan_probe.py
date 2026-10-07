import ast

src = open('backend/app/gateway/routers/bots.py', encoding='utf-8').read()
tree = ast.parse(src)
print('parsed ok')
c = 0
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        decs = [ast.unparse(d) for d in node.decorator_list]
        if decs:
            c += 1
print('funcs with decorators:', c)
