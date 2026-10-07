import re
src = open("backend/app/gateway/app.py", encoding="utf-8").read().splitlines()
# show include_router calls with dependencies
for i, line in enumerate(src, 1):
    if "include_router" in line:
        # extract dependencies= part
        m = re.search(r'dependencies=\[([^\]]*)\]', line)
        if m:
            print(i, line.strip())
            print("   deps:", m.group(1)[:200])
