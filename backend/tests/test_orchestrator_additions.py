"""Additive orchestrator tests (15 OpenClaw-inspired core features)."""

from alpha.orchestrator.acp_binding import AcpBindingRegistry
from alpha.orchestrator.approvals import ApprovalCustodyStore
from alpha.orchestrator.automations import AutomationDefinition, AutomationScheduler
from alpha.orchestrator.context_engine_plugin import ContextEnginePlugin
from alpha.orchestrator.durable_tasks import (
    ConcurrencyGovernor,
    DedupeCache,
    DeliveryQueue,
    DurableTaskRuntime,
    TokenBudgetGovernor,
)
from alpha.orchestrator.memory_recall import CrossThreadRecallConfig, build_recall_query
from alpha.orchestrator.provider_routing import (
    ChannelModelOverride,
    UtilityModelRouter,
    build_fallback_chain,
)
from alpha.orchestrator.secrets import is_secret_ref, redact_for_log, resolve_secret_refs
from alpha.orchestrator.sessions import SessionCatalog
from alpha.orchestrator.tracing import bind_trace_for_subagent


def test_context_engine_plugin_passthrough():
    class FakeWatchdog:
        def compact(self, messages):
            return messages[-1:]

    class FakeEngine:
        watchdog = FakeWatchdog()

        def assemble(self, system_prompt="", history_messages=None, **kw):
            from alpha.orchestrator.context_engine_plugin import (
                ContextEnginePlugin as _P,
            )

            assert isinstance(_P, type)
            msgs = [{"role": "system", "content": system_prompt}] + list(history_messages or [])

            class R:
                def __init__(self, messages):
                    self.messages = messages

            return R(msgs)

    plugin = ContextEnginePlugin()
    result = plugin.run_assemble(FakeEngine(), system_prompt="sys", history_messages=[{"role": "user", "content": "hi"}])
    assert len(result.messages) == 2


def test_provider_routing():
    assert build_fallback_chain("a", ["b", "a", ""]) == ["a", "b"]
    router = UtilityModelRouter(flagship_model="big", utility_model="small")
    assert router.route("title") == "small"
    assert router.route("reasoning") == "big"
    overrides = ChannelModelOverride(by_channel={"telegram": "mini"})
    assert overrides.resolve(channel="telegram", default="big") == "mini"
    assert overrides.resolve(channel="slack", default="big") == "big"


def test_approvals_custody_and_recurring():
    store = ApprovalCustodyStore(auto_mode=True)
    low = store.request(trace_id="t1", session_id="s1", user_id="u1", command="ls /tmp")
    assert low.outcome == "auto_allow"
    high = store.request(trace_id="t1", session_id="s1", user_id="u1", command="rm -rf /")
    assert high.outcome == "ask"
    store.grant_recurring("cron:nightly", "ls /tmp")
    again = store.request(trace_id="t2", session_id="s1", user_id="u1", command="ls /tmp", recurring_key="cron:nightly")
    assert again.outcome == "auto_allow"


def test_secret_refs():
    assert is_secret_ref("secretRef://API_KEY")
    out = resolve_secret_refs({"token": "secretRef://API_KEY"}, {"API_KEY": "shh"})
    assert out == {"token": "shh"}
    assert redact_for_log({"token": "shh"}, {"API_KEY": "shh"}) == {"token": "***"}


def test_durable_tasks_leases_and_recovery():
    runtime = DurableTaskRuntime(base_poll_seconds=0.01, max_poll_seconds=0.02)
    task = runtime.submit(kind="mcp", payload={"tool": "fetch"})
    claimed = runtime.claim_due(owner="w1")
    assert claimed and claimed[0].task_id == task.task_id
    runtime.report_retryable_error(task.task_id, "timeout")
    assert runtime.get(task.task_id).attempts == 1
    runtime.report_success(task.task_id, "ok")
    assert runtime.get(task.task_id).status == "succeeded"

    queue: DeliveryQueue = DeliveryQueue(maxsize=2)
    assert queue.offer({"a": 1}, idempotency_key="k1") is True
    assert queue.offer({"a": 1}, idempotency_key="k1") is False

    dedupe = DedupeCache(ttl_seconds=60)
    assert dedupe.seen("evt-1") is False
    assert dedupe.seen("evt-1") is True

    governor = ConcurrencyGovernor(max_running=1, max_queued=1)
    assert governor.try_acquire() == "run"
    assert governor.try_acquire() in ("queue", "reject")

    budget = TokenBudgetGovernor(max_tokens=100, warn_threshold=0.8)
    assert budget.observe(10) == "ok"
    assert budget.observe(85) == "warn"
    assert budget.observe(150) == "hard_stop"


def test_automations_non_interactive():
    scheduler = AutomationScheduler()
    definition = AutomationDefinition(name="nightly", kind="cron", cron="0 3 * * *")
    scheduler.register(definition)
    ctx = scheduler.run_context(definition.automation_id)
    assert ctx["non_interactive"] is True and ctx["disable_clarification"] is True


def test_recall_config_defaults_off():
    config = CrossThreadRecallConfig()
    assert config.enabled is False
    query = build_recall_query("hello", thread_id="t1")
    assert query["exclude_thread_id"] == "t1"


def test_sessions_catalog_branch_search():
    catalog = SessionCatalog()
    session = catalog.create(thread_id="th_1", title="main chat")
    assert catalog.bind_topic(session.session_id, "telegram", "topic-1") is True
    branch = catalog.branch(session.session_id)
    assert branch is not None and branch.parent_session_id == session.session_id
    assert catalog.search(query="main") != []


def test_tracing_helpers():
    child = bind_trace_for_subagent({"foo": "bar"}, "trace-123")
    assert child["agent_workspace_trace_id"] == "trace-123"


def test_acp_binding_registry():
    registry = AcpBindingRegistry()
    registry.bind("th_1", "codex")
    assert registry.resolve("th_1") == "codex"
    assert registry.resolve("unknown") == "lead_agent"
    assert registry.unbind("th_1") is True
