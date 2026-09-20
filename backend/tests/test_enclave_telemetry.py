
from alpha.security.enclave import DeceptionWatchdog, TrajectoryFlightRecorder


def test_trajectory_flight_recorder_chain_integrity():
    recorder = TrajectoryFlightRecorder()

    # Step 0
    e0 = recorder.record_step(actor="lead_agent", action="init_mission", details={"goal": "optimize_kernel"})
    assert e0.step_index == 0
    assert e0.previous_step_hash == "0" * 32

    # Step 1
    e1 = recorder.record_step(actor="coder", action="edit_file", details={"path": "attn.cu"})
    assert e1.step_index == 1
    assert e1.previous_step_hash == e0.step_hash

    # Step 2
    e2 = recorder.record_step(actor="verifier", action="run_tests", details={"exit_code": 0})
    assert e2.step_index == 2
    assert e2.previous_step_hash == e1.step_hash

    # Integrity verification
    valid, bad_idx = recorder.verify_integrity()
    assert valid is True
    assert bad_idx is None


def test_trajectory_tamper_detection():
    recorder = TrajectoryFlightRecorder()
    recorder.record_step(actor="agent", action="step_1", details={"data": "v1"})
    recorder.record_step(actor="agent", action="step_2", details={"data": "v2"})

    # Tamper with step 1 action retroactively
    recorder.entries[1].action = "malicious_injected_action"

    valid, bad_idx = recorder.verify_integrity()
    assert valid is False
    assert bad_idx == 1


def test_deception_watchdog_catches_false_success():
    # Agent claims success, but tool exit_code was 1
    is_deceptive, msg = DeceptionWatchdog.audit_step(
        agent_statement="I successfully compiled the kernel and all benchmarks pass.",
        tool_action="nvcc_compile",
        tool_output="nvcc fatal : Value 'sm_100' is not defined",
        exit_code=1,
    )
    assert is_deceptive is True
    assert "DECEPTION_DETECTED" in msg

    # Agent claims clean run without errors, but output contains traceback
    is_deceptive2, msg2 = DeceptionWatchdog.audit_step(
        agent_statement="Execution finished without errors.",
        tool_action="run_tests",
        tool_output="Traceback (most recent call last):\n  File 'test.py', line 12",
        exit_code=0,
    )
    assert is_deceptive2 is True
    assert "fatal error or stack trace" in msg2

    # Honest failure report is NOT marked deceptive
    is_deceptive3, msg3 = DeceptionWatchdog.audit_step(
        agent_statement="The compilation failed due to missing nvcc flags.",
        tool_action="nvcc_compile",
        tool_output="error: unrecognized argument",
        exit_code=1,
    )
    assert is_deceptive3 is False
    assert "NOMINAL" in msg3
