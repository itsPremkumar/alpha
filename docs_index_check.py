import json, sys
p = 'docs/INDEX.md'
with open(p, encoding='utf-8') as f:
    lines = f.readlines()
print('total lines:', len(lines))
# print any line mentioning a doc link
for i, ln in enumerate(lines, 1):
    if '- [' in ln:
        print(i, ln.rstrip()[:120])
