"""Tests for PatchSynthesizer, diff statistics, and patch hygiene validator."""

from alpha.workspace_changes.patch_synthesizer import (
    PatchSynthesizer,
)


def test_diff_stats_computation():
    synth = PatchSynthesizer()

    sample_patch = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -10,3 +10,5 @@ def run():
-    old_call()
+    new_call()
+    extra_call()
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-# Old
+# New
"""
    stats = synth.compute_diff_stats(sample_patch)
    assert stats["files_changed_count"] == 2
    assert "src/app.py" in stats["files_changed"]
    assert "README.md" in stats["files_changed"]
    assert stats["insertions"] == 3
    assert stats["deletions"] == 2
    assert stats["total_lines_altered"] == 5


def test_patch_hygiene_clean_patch():
    synth = PatchSynthesizer()
    clean_patch = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-print(1)
+print(2)
"""
    result = synth.validate_patch_hygiene(clean_patch)
    assert result.is_valid
    assert not result.has_errors
    assert len(result.errors) == 0


def test_patch_hygiene_secret_detection():
    synth = PatchSynthesizer()

    # Leaked AWS Key
    bad_aws_patch = """diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1 +1 @@
+AWS_KEY = "AKIA1234567890ABCDEF"
"""
    res_aws = synth.validate_patch_hygiene(bad_aws_patch)
    assert not res_aws.is_valid
    assert any("AWS Access Key" in err for err in res_aws.errors)

    # Leaked Private Key
    bad_pk_patch = """diff --git a/keys.pem b/keys.pem
--- a/keys.pem
+++ b/keys.pem
@@ -1 +1 @@
+-----BEGIN RSA PRIVATE KEY-----
+MIIEowIBAAKCAQEA0...
"""
    res_pk = synth.validate_patch_hygiene(bad_pk_patch)
    assert not res_pk.is_valid
    assert any("Private Key" in err for err in res_pk.errors)


def test_patch_hygiene_disallowed_extension():
    synth = PatchSynthesizer()
    bad_ext_patch = """diff --git a/app.pyc b/app.pyc
--- a/app.pyc
+++ b/app.pyc
@@ -1 +1 @@
+binary compiled python
"""
    res_ext = synth.validate_patch_hygiene(bad_ext_patch)
    assert not res_ext.is_valid
    assert any("Temporary/binary file" in err for err in res_ext.errors)
