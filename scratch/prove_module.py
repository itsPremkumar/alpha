import importlib.util

spec = importlib.util.spec_from_file_location("m", "backend/packages/harness/alpha/egress/policy.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

for d in ["example.com", "evil.com\r\n", "evil.com\n", "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("REJECT", repr(d), "->", e)
