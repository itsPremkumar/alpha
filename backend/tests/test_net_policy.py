from alpha.safety.net_policy import NetworkPolicyGuard


def test_net_policy_blocks_loopback_and_private():
    guard = NetworkPolicyGuard()

    # 1. Loopback
    res = guard.validate_url("http://127.0.0.1:8080/admin")
    assert res.allowed is False
    assert "forbidden subnet" in res.reason or "SSRF" in res.reason

    res_local = guard.validate_url("http://localhost:3000")
    assert res_local.allowed is False
    assert "forbidden host" in res_local.reason

    # 2. RFC 1918 private subnets
    assert guard.is_url_allowed("http://10.0.0.5/api") is False
    assert guard.is_url_allowed("http://172.16.0.1:5000") is False
    assert guard.is_url_allowed("http://192.168.1.100/status") is False


def test_net_policy_blocks_cloud_metadata():
    guard = NetworkPolicyGuard()

    # AWS / Azure / OpenStack metadata
    res_aws = guard.validate_url("http://169.254.169.254/latest/meta-data/")
    assert res_aws.allowed is False
    assert "forbidden subnet" in res_aws.reason

    # GCP metadata host
    res_gcp = guard.validate_url("http://metadata.google.internal/computeMetadata/v1/")
    assert res_gcp.allowed is False
    assert "forbidden host" in res_gcp.reason


def test_net_policy_blocks_disallowed_schemes():
    guard = NetworkPolicyGuard()

    res_file = guard.validate_url("file:///etc/passwd")
    assert res_file.allowed is False
    assert "Forbidden URL scheme" in res_file.reason

    res_ftp = guard.validate_url("ftp://ftp.example.com/file.zip")
    assert res_ftp.allowed is False


def test_net_policy_permits_public_urls():
    guard = NetworkPolicyGuard()

    res_pub = guard.validate_url("https://api.github.com/repos/openclaw")
    assert res_pub.allowed is True
    assert res_pub.reason == "URL egress permitted"
