import os

import pytest

from agent_workspace.security.enclave import BoundaryViolationError, TaskBoundaryPolicy


def test_boundary_validates_enclave_path(tmp_path):
    safe_root = str(tmp_path / "sandbox")
    os.makedirs(safe_root, exist_ok=True)
    policy = TaskBoundaryPolicy(allowed_root_paths=[safe_root])

    # Valid child path
    child_path = os.path.join(safe_root, "subdir", "file.txt")
    resolved = policy.validate_path(child_path)
    assert resolved == os.path.abspath(child_path)

    # Traversal escape outside safe_root -> must raise BoundaryViolationError
    escape_path = os.path.join(safe_root, "..", "escape.txt")
    with pytest.raises(BoundaryViolationError):
        policy.validate_path(escape_path)


def test_boundary_validates_network_target():
    policy = TaskBoundaryPolicy(allowed_domains=["github.com", "pypi.org"])

    # Allowed domain
    assert policy.validate_network_target("https://github.com/bytedance/agent-workspace") is True
    assert policy.validate_network_target("pypi.org") is True

    # Cloud metadata endpoint -> strictly blocked with error
    with pytest.raises(BoundaryViolationError):
        policy.validate_network_target("http://169.254.169.254/latest/meta-data")

    # Unlisted external domain -> returns False
    assert policy.validate_network_target("https://untrusted-external-site.com") is False


def test_boundary_mutation_and_depth_limits(tmp_path):
    policy = TaskBoundaryPolicy(
        allowed_root_paths=[str(tmp_path)],
        max_subagent_depth=2,
        max_mutations=2,
    )

    # Subagent depth validation
    assert policy.validate_subagent_depth(0) is True
    assert policy.validate_subagent_depth(1) is True
    with pytest.raises(BoundaryViolationError):
        policy.validate_subagent_depth(2)

    # Mutation limits
    assert policy.record_mutation() == 1
    assert policy.record_mutation() == 2
    with pytest.raises(BoundaryViolationError):
        policy.record_mutation()
