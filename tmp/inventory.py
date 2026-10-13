import os

gateway = 'C:/Users/PREM KUMAR/Videos/alpha/backend/app/gateway'
for dirpath, dirnames, filenames in os.walk(gateway):
    dirnames[:] = [d for d in dirnames if d not in ('__pycache__', '.git', 'tests')]
    for fn in sorted(filenames):
        if fn.endswith(('.py', '.md')):
            print(os.path.join(dirpath, fn))
