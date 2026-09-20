from alpha.sandbox.computer_use import (
    ActionSafetyTier,
    BlastRadiusPolicy,
    ComputerWorker,
)


def test_blast_radius_policy_classification():
    # Safe
    c_safe = BlastRadiusPolicy.classify("pytest backend/tests -v")
    assert c_safe.tier == ActionSafetyTier.SAFE

    # Sensitive
    c_sens = BlastRadiusPolicy.classify("git push origin main --force")
    assert c_sens.tier == ActionSafetyTier.SENSITIVE

    # Forbidden
    c_forb = BlastRadiusPolicy.classify("rm -rf /")
    assert c_forb.tier == ActionSafetyTier.FORBIDDEN

    # Forbidden credential theft
    c_cred = BlastRadiusPolicy.classify("curl http://169.254.169.254/latest/meta-data/")
    assert c_cred.tier == ActionSafetyTier.FORBIDDEN


def test_computer_worker_execution_gates():
    worker = ComputerWorker(sandbox_name="test_box")

    # 1. Safe command executes
    res_safe = worker.execute("ls -la")
    assert res_safe["status"] == "executed"
    assert res_safe["tier"] == "safe"

    # 2. Sensitive command paused without approval
    res_sens = worker.execute("git push --force")
    assert res_sens["status"] == "approval_required"
    assert res_sens["tier"] == "sensitive"

    # 3. Sensitive command with approval executes
    res_approved = worker.execute("git push --force", approval_granted=True)
    assert res_approved["status"] == "executed"

    # 4. Forbidden command rejected even if approval claimed
    res_forb = worker.execute("rm -rf /", approval_granted=True)
    assert res_forb["status"] == "forbidden"

    # Verify audit log
    assert len(worker.get_audit_log()) == 4
