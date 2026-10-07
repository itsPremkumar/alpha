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
pat = re.compile(r're\.(match|search|fullmatch)\(\s*(r?\"[^\"]*\\\$|r?[^\"]*\\\\\$\')')
hits = []
for root, dirs, files in os.walk(scope):
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
            if pat.search(line):
                hits.append((p, i, line.strip()))
print('--- $-anchored regex hits ---')
for h in hits:
    print(h)
print('count:', len(hits))
