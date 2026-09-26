"""Unit tests for the bounded Prometheus metrics registry (alpha.ops.metrics)."""

from __future__ import annotations

import pytest

from alpha.ops.metrics import (
    CONTENT_TYPE,
    MAX_SERIES,
    SERIES_DROPPED_NAME,
    MetricsRegistry,
    get_metrics_registry,
    publish_process_metrics,
)


def test_exposition_content_type_matches_prometheus_text_format() -> None:
    assert CONTENT_TYPE == "text/plain; version=0.0.4; charset=utf-8"


def test_default_cap_matches_upstream_contract() -> None:
    assert MAX_SERIES == 2048


def test_constructor_rejects_nonpositive_cap() -> None:
    with pytest.raises(ValueError):
        MetricsRegistry(max_series=0)


def test_counter_declares_renders_and_accumulates() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_test_total", help="A test counter", labels=("kind",))
    counter.inc(2.0, kind="alpha")
    counter.inc(3.0, kind="alpha")
    counter.inc(1.0, kind="beta")
    text = registry.render_prometheus()
    assert "# TYPE alpha_test_total counter" in text
    assert 'alpha_test_total{kind="alpha"} 5' in text
    assert 'alpha_test_total{kind="beta"} 1' in text


def test_gauge_set_overwrites_and_inc_adds() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("alpha_test_gauge", help="A test gauge")
    gauge.set(7.0)
    gauge.set(9.0)
    assert "alpha_test_gauge 9" in registry.render_prometheus()
    gauge.inc(1.0)
    assert "alpha_test_gauge 10" in registry.render_prometheus()


def test_counters_refuse_negative_increments() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_test_negative_total", help="neg")
    with pytest.raises(ValueError):
        counter.inc(-1.0)


def test_gauge_inc_negative_is_allowed() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("alpha_test_gauge_down", help="down")
    gauge.set(5.0)
    gauge.inc(-2.0)
    assert "alpha_test_gauge_down 3" in registry.render_prometheus()


def test_type_mismatch_between_operations_is_refused() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("alpha_test_hist_seconds", help="h")
    with pytest.raises(ValueError):
        histogram.inc(1.0)  # type: ignore[call-arg]
    gauge = registry.gauge("alpha_test_gauge_type", help="g")
    with pytest.raises(ValueError):
        gauge.observe(1.0)  # type: ignore[call-arg]


def test_undeclared_labels_are_refused() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_test_labels_total", help="l", labels=("kind",))
    with pytest.raises(ValueError):
        counter.inc(1.0, wrong_label="x")
    # A declared label omitted at the call site falls back to "" (bounded,
    # never a KeyError) rather than being refused.
    counter.inc(1.0)
    assert 'alpha_test_labels_total{kind=""} 1' in registry.render_prometheus()


def test_metric_and_label_names_are_validated() -> None:
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.counter("bad-metric-name", help="b")
    with pytest.raises(ValueError):
        registry.counter("alpha_ok_total", help="b", labels=("bad-label",))
    with pytest.raises(ValueError):
        registry.counter("alpha_dup_total", help="b", labels=("a", "a"))


def test_redeclaration_is_idempotent_but_conflicting_redeclaration_raises() -> None:
    registry = MetricsRegistry()
    first = registry.counter("alpha_idem_total", help="first", labels=("kind",))
    second = registry.counter("alpha_idem_total", help="second", labels=("kind",))
    assert first.name == second.name == "alpha_idem_total"
    with pytest.raises(ValueError):
        registry.gauge("alpha_idem_total", help="type change", labels=("kind",))
    with pytest.raises(ValueError):
        registry.counter("alpha_idem_total", help="label change", labels=("other",))


def test_series_cap_refuses_new_series_and_bumps_drop_counter() -> None:
    registry = MetricsRegistry(max_series=2)
    counter = registry.counter("alpha_cap_total", help="cap", labels=("slot",))
    counter.inc(1.0, slot="a")
    counter.inc(1.0, slot="b")
    assert registry.series_count() == 2
    # Third distinct series is refused; the drop counter absorbs it (it is
    # itself exempt from the cap).
    counter.inc(1.0, slot="c")
    # 2 admitted user series + the cap-exempt drop-counter series itself —
    # series_count() counts every real series in the registry, and the drop
    # counter genuinely occupies one once bumped.
    assert registry.series_count() == 3
    text = registry.render_prometheus()
    assert 'alpha_cap_total{slot="a"} 1' in text
    assert 'alpha_cap_total{slot="b"} 1' in text
    assert 'alpha_cap_total{slot="c"}' not in text
    assert f"{SERIES_DROPPED_NAME} 1" in text


def test_drop_counter_absent_means_zero_drops() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_nodrop_total", help="n", labels=("kind",))
    counter.inc(1.0, kind="x")
    text = registry.render_prometheus()
    assert SERIES_DROPPED_NAME not in text


def test_label_values_longer_than_policy_collapse_to_other() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_long_total", help="long", labels=("slot",))
    counter.inc(1.0, slot="x" * 200)
    text = registry.render_prometheus()
    assert 'alpha_long_total{slot="other"} 1' in text
    assert "x" * 65 not in text


def test_label_values_with_control_characters_collapse_to_other() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_ctrl_total", help="ctrl", labels=("slot",))
    counter.inc(1.0, slot="a\nb")
    assert 'alpha_ctrl_total{slot="other"} 1' in registry.render_prometheus()


def test_quotes_and_backslashes_are_escaped_at_render_time() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_esc_total", help="esc", labels=("slot",))
    counter.inc(1.0, slot='a"b\\c')
    text = registry.render_prometheus()
    assert 'alpha_esc_total{slot="a\\"b\\\\c"} 1' in text


def test_histogram_renders_buckets_sum_and_count() -> None:
    registry = MetricsRegistry()
    histogram = registry.histogram("alpha_hist_seconds", help="h", labels=("route",), buckets=(0.1, 1.0))
    histogram.observe(0.05, route="/a")
    histogram.observe(0.5, route="/a")
    histogram.observe(5.0, route="/a")
    text = registry.render_prometheus()
    assert "# TYPE alpha_hist_seconds histogram" in text
    assert 'alpha_hist_seconds_bucket{route="/a",le="0.1"} 1' in text
    assert 'alpha_hist_seconds_bucket{route="/a",le="1"} 2' in text
    assert 'alpha_hist_seconds_bucket{route="/a",le="+Inf"} 3' in text
    assert 'alpha_hist_seconds_count{route="/a"} 3' in text
    assert 'alpha_hist_seconds_sum{route="/a"} 5.55' in text


def test_histogram_bucket_validation() -> None:
    registry = MetricsRegistry()
    with pytest.raises(ValueError):
        registry.histogram("alpha_bad_buckets_seconds", help="b", buckets=(1.0, 0.5))
    with pytest.raises(ValueError):
        registry.histogram("alpha_neg_buckets_seconds", help="b", buckets=(-1.0,))


def test_remove_clears_one_series_and_remove_all_clears_the_metric() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("alpha_remove_total", help="r", labels=("kind",))
    counter.inc(1.0, kind="a")
    counter.inc(1.0, kind="b")
    counter.remove(kind="a")
    text = registry.render_prometheus()
    assert 'alpha_remove_total{kind="a"}' not in text
    assert 'alpha_remove_total{kind="b"} 1' in text
    counter.remove_all()
    assert "alpha_remove_total" not in registry.render_prometheus()


def test_process_metrics_publish_uptime_gauge() -> None:
    registry = MetricsRegistry()
    publish_process_metrics(registry)
    text = registry.render_prometheus()
    assert "# TYPE alpha_process_uptime_seconds gauge" in text
    assert "alpha_process_uptime_seconds " in text


def test_global_registry_singleton() -> None:
    assert get_metrics_registry() is get_metrics_registry()
