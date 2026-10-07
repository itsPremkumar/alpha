import re
src = open("backend/app/gateway/app.py", encoding="utf-8").read().splitlines()
for i, line in enumerate(src, 1):
    if "include_router" in line:
        print(i, line.strip())
