import json
from collections import Counter

p = 'contracts/feature_manifest.json'
with open(p, encoding='utf-8') as f:
    d = json.load(f)
print('top-level keys:', list(d.keys()))
for sec in ('tools','routers','middlewares','loops','engines'):
    entries = d.get(sec) or []
    print(sec, 'count=', len(entries))
    state = Counter(e.get('state','') for e in entries)
    print('   states:', dict(state))
print('version:', d.get('version'), 'generated_at:', d.get('generated_at'))
