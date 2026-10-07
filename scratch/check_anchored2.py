import os
import re

scope = [
    'backend/packages/harness/alpha/tools',
    'backend/packages/harness/alpha/sandbox',
    'backend/packages/harness/alpha/security',
    'backend/packages/harness/alpha/safety',
    'backend/packages/harness/alpha/guardrails',
    'backend/packages/harness/alpha/authz',
    'backend/packages/harness/alpha/egress',
    'backend/packages/harness/alpha/policy',
    'backend/packages/harness/alpha/computer_use',
    'backend/app/gateway/auth',
]

# Pattern: re.match / re.search / re.fullmatch with a literal trailing $
# We match the call and then check the pattern string ends with $
pat = re.compile(r're\.(match|search|fullmatch)\(\s*(r?)"([^"]*)"')

hits = []
for root in scope:
    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.venv', 'node_modules', 'build', 'dist')]
    for f in files:
        if not f.endswith('.py'):
            continue
        p = os.path.join(root, f)
        try:
            txt = open(p, encoding='utf-8').read()
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            m = pat.search(line)
            if m and m.group(2) != '' and m.group(2).rstrip().endswith('$'):
                hits.append((p, i, line.strip()))
print('--- $-anchored regex hits ---')
for h in hits:
    print(h)
print('count:', len(hits))
