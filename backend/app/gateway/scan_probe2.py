import ast
src = open('backend/app/gateway/routers/bots.py', encoding='utf-8').read()
tree = ast.parse(src)
c = 0
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        decs = [ast.unparse(d) for d in node.decorator_list]
        if decs:
            c += 1
            if any('router' in d for d in decs[:1]):
                print('router dec found')
                print(decs[:3])
print('funcs with decorators:', c)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == 'list_bot_templates':
        print('decorators:', [ast.unparse(d) for d in node.decorator_list])
        break
