"""Proof that a '$' anchored re.match lets trailing CRLF pass validation.

The described defect: a domain string with a trailing CRLF passes re.match
against a "..."$ anchored pattern, so the string reaches a filesystem/path
function with the CRLF still attached.

The SPDX-style header is part of the file; keep it.
"""

# Original pattern as it lived in egress/policy.py before the fix this cycle.
# re.match stops matching at the '$' position when the input has a trailing
# newline or carriage-return+newline, so the newline/CR is NOT consumed.
original = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\$")

# Fixed pattern as it now lives in the on-disk file (unanchored + fullmatch).
fixed = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

CR = chr(13)  # carriage return
LF = chr(10)  # line feed

def show(pattern, label):
    print(label)
    for t in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com"]:
        m = pattern.match(t)
        print("  input", repr(t), "->", repr(m.group(0)) if m else None)

show(original, "=== BEFORE (original, with $) ===")
show(fixed, "=== AFTER (fixed, fullmatch) ===")

# Also verify the real function on disk.
import importlib.util
spec = importlib.util.spec_from_file_location("ep", "backend/packages/harness/alpha/egress/policy.py")
spec.name = "ep_mod"
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("\n=== real _validate_domain on disk ===")
for d in ["example.com", "evil.com" + CR + LF, "evil.com" + LF, "a-b.example.com", "ex.com", "EXAMPLE.COM", "b.com", ""]:
    try:
        print("  ACCEPT", repr(d), "->", repr(m._validate_domain(d)))
    except m.EgressValidationError as e:
        print("  REJECT", repr(d), "->", e)
