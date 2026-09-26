"""Deterministic-first narrative chapter synthesis with optional model refinement.

The default path is intentionally boring and inspectable: events are ordered,
bucketed into day/week/month periods, and rendered as title + top outcomes.  A
caller may inject a model seam for prose refinement, but malformed or failed
model output is disclosed and the previously persisted story is never replaced.
The incremental path reuses unchanged chapters and records the periods it
actually rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import NarrativeConfig
from .models import (
    SYNTHESIS_STATUSES,
    NarrativeEvent,
    RegenerationResult,
    RegenerationStatus,
    StoryChapter,
    StoryDocument,
    SynthesisOutcome,
    SynthesisStatus,
)
from .paths import scope_key
from .provenance import append_regeneration
from .store import NarrativeStore

_DAY_SECONDS = 86_400.0
_MAX_PROMPT_EVENTS = 100
_MAX_MODEL_RESPONSE_CHARS = 100_000

KNOWN_SYNTHESIS_STATUSES = SYNTHESIS_STATUSES


def synthesis_status_known(status: SynthesisStatus | str) -> bool:
    """Whether a model-refinement status belongs to the closed set."""

    value = status.value if isinstance(status, SynthesisStatus) else str(status)
    return value in SYNTHESIS_STATUSES


def _event_list(events: Iterable[NarrativeEvent | Mapping[str, Any]]) -> list[NarrativeEvent]:
    result: list[NarrativeEvent] = []
    for event in events:
        result.append(event if isinstance(event, NarrativeEvent) else NarrativeEvent.model_validate(event))
    return result


def _eligible_events(
    events: Iterable[NarrativeEvent],
    config: NarrativeConfig,
    now: float,
) -> list[NarrativeEvent]:
    """Apply the configured importance and lookback readers before synthesis."""

    cutoff = now - (config.lookback_days * _DAY_SECONDS)
    return [event for event in events if event.importance >= config.min_importance and event.period_end >= cutoff]


def choose_bucket(events: Iterable[NarrativeEvent | Mapping[str, Any]]) -> str:
    """Choose day, week, or month from the chronological event span."""

    materialized = _event_list(events)
    if not materialized:
        return "day"
    span = max(event.period_end for event in materialized) - min(event.period_start for event in materialized)
    if span <= 2 * _DAY_SECONDS:
        return "day"
    if span <= 31 * _DAY_SECONDS:
        return "week"
    return "month"


def _period_label(timestamp: float, bucket: str) -> str:
    moment = datetime.fromtimestamp(timestamp, tz=UTC)
    if bucket == "day":
        return moment.strftime("%Y-%m-%d")
    if bucket == "week":
        iso = moment.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    return moment.strftime("%Y-%m")


def _period_bounds(events: list[NarrativeEvent], period: str, bucket: str) -> tuple[float, float]:
    if not events:
        return 0.0, 0.0
    if bucket == "day":
        start = datetime.fromtimestamp(min(event.period_start for event in events), tz=UTC)
        end = datetime.fromtimestamp(max(event.period_end for event in events), tz=UTC)
        return start.timestamp(), end.timestamp()
    if bucket == "week":
        first = min(events, key=lambda event: event.period_start)
        last = max(events, key=lambda event: event.period_end)
        moment = datetime.fromtimestamp(first.period_start, tz=UTC)
        start = moment - timedelta(days=moment.weekday() + 1)
        end = datetime.fromtimestamp(last.period_end, tz=UTC)
        return start.timestamp(), end.timestamp()
    first = min(events, key=lambda event: event.period_start)
    last = max(events, key=lambda event: event.period_end)
    return first.period_start, last.period_end


def _source_fingerprint(events: list[NarrativeEvent]) -> str:
    material = "|".join(event.fingerprint() for event in events)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _clean_entry_text(value: Any) -> str:
    return " ".join(str(value or "").split())[:2_000]


def deterministic_entry(event: NarrativeEvent) -> str:
    """Build an honest fallback line from the event's title and top outcomes."""

    pieces = [event.title]
    if event.summary and event.summary.casefold() != event.title.casefold():
        pieces.append(event.summary)
    if event.participants:
        pieces.append("participants: " + "; ".join(event.participants[:5]))
    outcomes = [outcome for outcome in event.outcomes if outcome.strip()][:3]
    if outcomes:
        pieces.append("outcomes: " + "; ".join(outcomes))
    return " — ".join(pieces)


def deterministic_chapter(period: str, events: list[NarrativeEvent], bucket: str) -> StoryChapter:
    """Build one deterministic chapter from an already ordered event group."""

    ordered = sorted(events, key=lambda event: (event.period_start, event.period_end, event.id))
    start, end = _period_bounds(ordered, period, bucket)
    entries = [deterministic_entry(event) for event in ordered]
    summary = "; ".join(f"{event.title}: {outcome}" for event in ordered for outcome in event.outcomes[:1] if outcome.strip())
    if not summary:
        summary = "; ".join(event.title for event in ordered)
    return StoryChapter(
        heading=f"{period} — {len(ordered)} moment{'s' if len(ordered) != 1 else ''}",
        period=period,
        entries=entries,
        summary=summary[:4_000],
        event_ids=[event.id for event in ordered],
        period_start=start,
        period_end=end,
        synthesis="deterministic",
        source_fingerprint=_source_fingerprint(ordered),
    )


def _group_events(events: list[NarrativeEvent], bucket: str | None = None) -> tuple[str, dict[str, list[NarrativeEvent]]]:
    selected_bucket = bucket or choose_bucket(events)
    if selected_bucket not in {"day", "week", "month"}:
        raise ValueError("bucket must be day, week, or month")
    groups: dict[str, list[NarrativeEvent]] = defaultdict(list)
    for event in events:
        groups[_period_label(event.period_start, selected_bucket)].append(event)
    return selected_bucket, dict(groups)


def _render_document(
    *,
    scope: str,
    scope_id: str,
    chapters: list[StoryChapter],
    total_source_events: int,
    generated_at: float,
    synthesis: str,
    changed_chapters: list[str],
    disclosures: list[str],
    truncated: bool,
) -> str:
    """Render a document with a stable, inspectable disclosure footer."""

    lines = [f"# {scope} story", f"synthesis={synthesis}"]
    for chapter in chapters:
        lines.append(chapter.render())
    if truncated:
        lines.append("[disclosure: story truncated to configured max_chars]")
    if disclosures:
        lines.append("disclosures: " + ", ".join(dict.fromkeys(disclosures)))
    lines.append(f"source_event_count={total_source_events}")
    return "\n".join(lines)


def _fit_document(
    *,
    scope: str,
    scope_id: str,
    chapters: list[StoryChapter],
    total_source_events: int,
    generated_at: float,
    synthesis: str,
    changed_chapters: list[str],
    max_chars: int,
    dropped_periods: list[str],
) -> StoryDocument:
    """Build a document and deterministically disclose any character trimming."""

    disclosures = list(dropped_periods)
    working = [chapter.model_copy(deep=True) for chapter in chapters]
    truncated = False
    rendered = _render_document(
        scope=scope,
        scope_id=scope_id,
        chapters=working,
        total_source_events=total_source_events,
        generated_at=generated_at,
        synthesis=synthesis,
        changed_chapters=changed_chapters,
        disclosures=disclosures,
        truncated=False,
    )
    if len(rendered) > max_chars:
        truncated = True
        disclosures.append("max_chars")
        while (
            len(
                _render_document(
                    scope=scope,
                    scope_id=scope_id,
                    chapters=working,
                    total_source_events=total_source_events,
                    generated_at=generated_at,
                    synthesis=synthesis,
                    changed_chapters=changed_chapters,
                    disclosures=disclosures,
                    truncated=truncated,
                )
            )
            > max_chars
            and working
        ):
            removable = [chapter for chapter in working if chapter.entries]
            if removable:
                removable[-1].entries.pop()
                removable[-1].truncated = True
                removable[-1].clamped_fields = list(dict.fromkeys([*removable[-1].clamped_fields, "entries_truncated"]))
                continue
            working.pop()
        rendered = _render_document(
            scope=scope,
            scope_id=scope_id,
            chapters=working,
            total_source_events=total_source_events,
            generated_at=generated_at,
            synthesis=synthesis,
            changed_chapters=changed_chapters,
            disclosures=disclosures,
            truncated=truncated,
        )
        if len(rendered) > max_chars:
            marker = "\n[disclosure: story truncated to configured max_chars]"
            if max_chars <= len(marker):
                rendered = rendered[:max_chars]
            else:
                rendered = rendered[: max_chars - len(marker)].rstrip() + marker
    document = StoryDocument(
        scope=scope,
        scope_id=scope_id,
        chapters=working,
        total_chars=len(rendered),
        generated_at=generated_at,
        source_event_count=total_source_events,
        synthesis=synthesis,
        changed_chapters=list(changed_chapters),
        truncated=truncated,
        disclosures=list(dict.fromkeys(disclosures)),
        clamped_fields=list(dict.fromkeys(disclosures)) if truncated else [],
        rendered_text=rendered,
    )
    return document


def build_chapters(
    events: Iterable[NarrativeEvent | Mapping[str, Any]],
    max_chars: int | NarrativeConfig = 8_000,
    max_chapters: int = 32,
    min_importance: float = 0.0,
    lookback_days: float | None = None,
    *,
    now: float | None = None,
    scope: str = "user",
    scope_id: str = "default",
    bucket: str | None = None,
) -> list[StoryChapter]:
    """Build bounded deterministic chapters from source events.

    Passing a :class:`NarrativeConfig` as the second positional argument is a
    convenience for callers that want all policy values to travel together.
    """

    config: NarrativeConfig | None = None
    if isinstance(max_chars, NarrativeConfig):
        config = max_chars
        max_chars = config.max_chars
        max_chapters = config.max_chapters
        min_importance = config.min_importance
        lookback_days = config.lookback_days
    materialized = _event_list(events)
    if now is None and materialized:
        moment = max(event.period_end for event in materialized)
    else:
        moment = time.time() if now is None else float(now)
    if config is not None:
        eligible = _eligible_events(materialized, config, moment)
    else:
        cutoff = moment - ((lookback_days or 0.0) * _DAY_SECONDS) if lookback_days is not None else None
        eligible = [event for event in materialized if event.importance >= min_importance]
        if cutoff is not None:
            eligible = [event for event in eligible if event.period_end >= cutoff]
    if not eligible:
        return []
    chapter_limit = max(0, int(max_chapters))
    if chapter_limit == 0:
        return []
    bucket, groups = _group_events(eligible, bucket)
    ordered_periods = sorted(groups, key=lambda period: min(event.period_start for event in groups[period]))
    dropped: list[str] = []
    if len(ordered_periods) > chapter_limit:
        dropped = ordered_periods[:-chapter_limit]
        ordered_periods = ordered_periods[-chapter_limit:]
    chapters = [deterministic_chapter(period, groups[period], bucket) for period in ordered_periods]
    # Keep a disclosure in the chapter metadata when direct callers use the
    # helper without a document wrapper.
    if dropped:
        for chapter in chapters:
            chapter.clamped_fields = list(dict.fromkeys([*chapter.clamped_fields, "chapters_dropped"]))
    if isinstance(max_chars, int) and max_chars > 0:
        fitted = _fit_document(
            scope=str(scope),
            scope_id=scope_id,
            chapters=chapters,
            total_source_events=len(eligible),
            generated_at=moment,
            synthesis="deterministic",
            changed_chapters=[],
            max_chars=max_chars,
            dropped_periods=dropped,
        )
        return fitted.chapters
    return chapters


def _response_text(response: Any) -> str:
    """Extract text from common LangChain-like responses without guessing JSON."""

    if isinstance(response, str):
        return response
    if isinstance(response, (list, tuple)):
        return json.dumps(response, ensure_ascii=False)
    if isinstance(response, Mapping):
        for key in ("content", "text", "output"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [str(item.get("text", "")) if isinstance(item, Mapping) else str(item) for item in value]
                return "".join(parts)
        try:
            return json.dumps(response, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    for key in ("content", "text"):
        value = getattr(response, key, None)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [str(item.get("text", "")) if isinstance(item, Mapping) else str(item) for item in value]
            return "".join(parts)
    return ""


def _model_draft(
    raw: str,
    *,
    period: str,
    heading: str,
    event_ids: list[str],
    period_start: float,
    period_end: float,
    fingerprint: str,
) -> SynthesisOutcome:
    """Parse strict model JSON and turn it into one bounded chapter."""

    text = str(raw or "")
    if not text.strip():
        return SynthesisOutcome(status=SynthesisStatus.NO_JSON, raw=text)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return SynthesisOutcome(status=SynthesisStatus.PARSE_FAIL, raw=text[:_MAX_MODEL_RESPONSE_CHARS])
    if not isinstance(payload, list):
        return SynthesisOutcome(status=SynthesisStatus.NOT_ARRAY, raw=text[:_MAX_MODEL_RESPONSE_CHARS])
    if not payload:
        return SynthesisOutcome(status=SynthesisStatus.EMPTY, raw=text[:_MAX_MODEL_RESPONSE_CHARS])
    entries: list[str] = []
    output_heading = heading
    for item in payload:
        if isinstance(item, str):
            entries.append(_clean_entry_text(item))
            continue
        if not isinstance(item, Mapping):
            continue
        candidate_heading = item.get("heading") or item.get("title")
        if candidate_heading:
            output_heading = _clean_entry_text(candidate_heading) or output_heading
        raw_entries = item.get("entries")
        if raw_entries is None:
            raw_entries = item.get("summary") or item.get("text") or item.get("story")
        if isinstance(raw_entries, str):
            raw_entries = [raw_entries]
        if isinstance(raw_entries, (list, tuple)):
            entries.extend(_clean_entry_text(entry) for entry in raw_entries)
    entries = [entry for entry in entries if entry]
    if not entries:
        return SynthesisOutcome(status=SynthesisStatus.EMPTY, raw=text[:_MAX_MODEL_RESPONSE_CHARS])
    try:
        chapter = StoryChapter(
            heading=output_heading,
            period=period,
            entries=entries,
            summary=" ".join(entries)[:4_000],
            event_ids=event_ids,
            period_start=period_start,
            period_end=period_end,
            synthesis="model",
            source_fingerprint=fingerprint,
        )
    except (TypeError, ValueError) as exc:
        return SynthesisOutcome(
            status=SynthesisStatus.PARSE_FAIL,
            raw=text[:_MAX_MODEL_RESPONSE_CHARS],
            error=f"invalid_chapter: {str(exc)[:500]}",
        )
    return SynthesisOutcome(status=SynthesisStatus.OK, chapters=[chapter], raw=text[:_MAX_MODEL_RESPONSE_CHARS])


def parse_model_response(
    raw: str,
    *,
    period: str,
    heading: str,
    event_ids: list[str],
    period_start: float = 0.0,
    period_end: float = 0.0,
    fingerprint: str = "",
) -> SynthesisOutcome:
    """Public strict-JSON parser for one refined chapter."""

    return _model_draft(
        raw,
        period=period,
        heading=heading,
        event_ids=event_ids,
        period_start=period_start,
        period_end=period_end,
        fingerprint=fingerprint,
    )


def _invoke_model(model: Any, prompt: str, model_name: str | None) -> tuple[str, str]:
    """Call only the injected seam; return ``(text, error)`` without raising."""

    if model is None:
        return "", "no_model_configured"
    try:
        if callable(model):
            response = model(prompt)
        elif hasattr(model, "invoke"):
            try:
                response = model.invoke(
                    prompt,
                    config={"run_name": "narrative_synthesis", "model": model_name or ""},
                )
            except TypeError:
                response = model.invoke(prompt)
        else:
            return "", "model_seam_not_callable"
    except BaseException as exc:  # noqa: BLE001 - synthesis must disclose, never crash a turn
        return "", f"{type(exc).__name__}: {str(exc)[:500]}"
    try:
        return _response_text(response), ""
    except BaseException as exc:  # noqa: BLE001 - response adapters are untrusted
        return "", f"{type(exc).__name__}: {str(exc)[:500]}"


def _model_refine(
    model: Any,
    chapter: StoryChapter,
    events: list[NarrativeEvent],
    model_name: str | None,
) -> SynthesisOutcome:
    event_dicts = [event.model_dump(mode="json") for event in events[:_MAX_PROMPT_EVENTS]]
    prompt = (
        "Refine one chronological story chapter. Return strict JSON only: "
        'an array of objects with optional "heading" and "entries" string arrays. '
        "Do not invent events or source references.\n"
        f"Period: {chapter.period}\n"
        f"Deterministic chapter: {json.dumps(chapter.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"Source events: {json.dumps(event_dicts, ensure_ascii=False)}"
    )
    text, error = _invoke_model(model, prompt, model_name)
    if error:
        return SynthesisOutcome(status=SynthesisStatus.LLM_ERROR, error=error, model_name=model_name)
    return parse_model_response(
        text,
        period=chapter.period,
        heading=chapter.heading,
        event_ids=list(chapter.event_ids),
        period_start=chapter.period_start or 0.0,
        period_end=chapter.period_end or 0.0,
        fingerprint=chapter.source_fingerprint,
    )


def _source_equal(left: StoryChapter | None, right: StoryChapter) -> bool:
    """Whether a chapter's source event set/content is unchanged."""

    if left is None:
        return False
    return left.period == right.period and left.event_ids == right.event_ids and left.source_fingerprint == right.source_fingerprint


def _chapter_equal(left: StoryChapter | None, right: StoryChapter) -> bool:
    if left is None:
        return False
    return _source_equal(left, right) and left.heading == right.heading and left.entries == right.entries


def regenerate_story(
    store: NarrativeStore,
    scope: Any = "user",
    *,
    scope_id: str | None = None,
    config: NarrativeConfig | None = None,
    model: Any = None,
    now: float | None = None,
    record_provenance: bool = False,
) -> RegenerationResult:
    """Regenerate one scope while preserving the old story on every failure."""

    if hasattr(store, "store") and not isinstance(store, NarrativeStore):
        facade = store
        return facade.regenerate(scope, model=model, now=now)
    cfg = config if config is not None else store.config
    moment = time.time() if now is None else float(now)
    target_kind, target_id = scope_key(scope, scope_id).split(":", 1) if ":" in scope_key(scope, scope_id) else (scope_key(scope, scope_id), "default")
    with store.scope_lock(scope, scope_id):
        old = store.get_story(scope, scope_id=scope_id)
        events = store.list_events(scope, scope_id=scope_id)
        if not cfg.enabled:
            return RegenerationResult(
                status=RegenerationStatus.DISABLED.value,
                synthesis_status=SynthesisStatus.EMPTY.value,
                document=old,
                event_count=len(events),
                chars=old.total_chars if old is not None else 0,
                story_unchanged=True,
                error="narrative_memory_disabled",
            )
        eligible = _eligible_events(events, cfg, moment)
        if not eligible:
            return RegenerationResult(
                status=RegenerationStatus.EMPTY.value,
                synthesis_status=SynthesisStatus.EMPTY.value,
                document=old,
                event_count=len(events),
                chars=old.total_chars if old is not None else 0,
                story_unchanged=True,
                error="no_eligible_events",
            )
        bucket, groups = _group_events(eligible)
        periods = sorted(groups, key=lambda period: min(event.period_start for event in groups[period]))
        dropped_periods: list[str] = []
        if len(periods) > cfg.max_chapters:
            dropped_periods = periods[: -cfg.max_chapters]
            periods = periods[-cfg.max_chapters :]
        deterministic = {period: deterministic_chapter(period, groups[period], bucket) for period in periods}
        old_by_period = {chapter.period: chapter for chapter in old.chapters} if old is not None else {}
        affected = [period for period, chapter in deterministic.items() if not _source_equal(old_by_period.get(period), chapter)]
        refined = {period: (old_by_period[period] if period in old_by_period and _source_equal(old_by_period[period], deterministic[period]) else deterministic[period]) for period in periods}
        used_model = False
        if cfg.enable_model_synthesis:
            if model is None:
                old_chars = old.total_chars if old is not None else 0
                return RegenerationResult(
                    status=SynthesisStatus.LLM_ERROR.value,
                    synthesis_status=SynthesisStatus.LLM_ERROR.value,
                    document=old,
                    changed_chapters=[],
                    event_count=len(eligible),
                    chars=old_chars,
                    error="no_model_configured",
                    story_unchanged=True,
                )
            for period in affected:
                try:
                    outcome = _model_refine(
                        model,
                        deterministic[period],
                        sorted(groups[period], key=lambda event: (event.period_start, event.id)),
                        cfg.synthesis_model,
                    )
                except BaseException as exc:  # noqa: BLE001 - optional model seam is fail-closed
                    outcome = SynthesisOutcome(
                        status=SynthesisStatus.LLM_ERROR,
                        error=f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                if not outcome.ok:
                    old_chars = old.total_chars if old is not None else 0
                    return RegenerationResult(
                        status=outcome.status.value,
                        synthesis_status=outcome.status.value,
                        document=old,
                        changed_chapters=[],
                        event_count=len(eligible),
                        chars=old_chars,
                        error=outcome.error or outcome.status.value,
                        story_unchanged=True,
                    )
                refined[period] = outcome.chapters[0]
                used_model = True
        changed: list[str] = []
        if old is not None:
            removed_periods = [period for period in old_by_period if period not in periods]
            changed.extend(removed_periods)
        for period in periods:
            candidate = refined[period]
            previous = old_by_period.get(period)
            if not _chapter_equal(previous, candidate):
                changed.append(period)
        changed = list(dict.fromkeys(changed))
        if old is not None and not changed:
            return RegenerationResult(
                status=RegenerationStatus.UNCHANGED.value,
                synthesis_status=SynthesisStatus.OK.value,
                document=old,
                changed_chapters=[],
                event_count=len(eligible),
                chars=old.total_chars,
                story_unchanged=True,
            )
        synthesis_name = "model" if used_model and old is None else "mixed" if used_model else "deterministic"
        if old is not None and any(chapter.synthesis == "model" for chapter in old.chapters) and not used_model:
            synthesis_name = "mixed"
        new_document = _fit_document(
            scope=target_kind,
            scope_id=target_id,
            chapters=[refined[period] for period in periods],
            total_source_events=len(eligible),
            generated_at=moment,
            synthesis=synthesis_name,
            changed_chapters=changed,
            max_chars=cfg.max_chars,
            dropped_periods=[f"dropped_period:{period}" for period in dropped_periods],
        )
        try:
            store.save_story(new_document, scope, scope_id=scope_id)
        except Exception as exc:  # noqa: BLE001 - preserve old document and disclose write failure
            return RegenerationResult(
                status=RegenerationStatus.STORE_ERROR.value,
                synthesis_status=SynthesisStatus.LLM_ERROR.value,
                document=old,
                changed_chapters=[],
                event_count=len(eligible),
                chars=old.total_chars if old is not None else 0,
                error=f"write_failed: {str(exc)[:500]}",
                story_unchanged=True,
            )
        result = RegenerationResult(
            status=RegenerationStatus.OK.value,
            synthesis_status=SynthesisStatus.OK.value,
            document=new_document,
            changed_chapters=changed,
            event_count=len(eligible),
            chars=new_document.total_chars,
            story_unchanged=False,
        )
        if record_provenance:
            append_regeneration(
                scope=scope,
                scope_id=scope_id,
                event_count=len(eligible),
                chapters_changed=changed,
                chars=new_document.total_chars,
                status=result.status,
                synthesis_status=result.synthesis_status,
                synthesis=new_document.synthesis,
                storage_path=str(store.root),
                now=moment,
            )
        return result


def regenerate(*args: Any, **kwargs: Any) -> RegenerationResult:
    """Public wrapper that records provenance for a direct regeneration call."""

    if args and hasattr(args[0], "regenerate") and not isinstance(args[0], NarrativeStore):
        return args[0].regenerate(*args[1:], **kwargs)
    kwargs.setdefault("record_provenance", True)
    return regenerate_story(*args, **kwargs)


def _synthesize_event_list(
    events: Iterable[NarrativeEvent | Mapping[str, Any]],
    *,
    config: NarrativeConfig | None,
    model: Any,
    now: float | None,
) -> SynthesisOutcome:
    """Model-friendly pure synthesis helper for callers not persisting yet."""

    cfg = config if config is not None else NarrativeConfig(enabled=True, min_importance=0.0, lookback_days=0.0)
    materialized = _event_list(events)
    effective_now = now
    if effective_now is None and materialized:
        effective_now = max(event.period_end for event in materialized)
    chapters = build_chapters(materialized, cfg, now=effective_now)
    if not chapters:
        return SynthesisOutcome(status=SynthesisStatus.EMPTY, model_name=cfg.synthesis_model)
    if not cfg.enable_model_synthesis:
        return SynthesisOutcome(
            status=SynthesisStatus.OK,
            chapters=chapters,
            changed_chapters=[chapter.period for chapter in chapters],
            model_name=cfg.synthesis_model,
        )
    if model is None:
        return SynthesisOutcome(
            status=SynthesisStatus.LLM_ERROR,
            error="no_model_configured",
            model_name=cfg.synthesis_model,
        )
    by_id = {event.id: event for event in materialized}
    refined: list[StoryChapter] = []
    for chapter in chapters:
        source_events = [by_id[event_id] for event_id in chapter.event_ids if event_id in by_id]
        outcome = _model_refine(model, chapter, source_events, cfg.synthesis_model)
        if not outcome.ok:
            return SynthesisOutcome(
                status=outcome.status,
                raw=outcome.raw,
                error=outcome.error,
                model_name=cfg.synthesis_model,
            )
        refined.append(outcome.chapters[0])
    return SynthesisOutcome(
        status=SynthesisStatus.OK,
        chapters=refined,
        changed_chapters=[chapter.period for chapter in refined],
        model_name=cfg.synthesis_model,
    )


def synthesize(*args: Any, **kwargs: Any) -> Any:
    """Synthesize raw events or regenerate a persisted scope.

    ``synthesize(events, config=..., model=...)`` is a pure, non-writing
    convenience API.  Passing a :class:`NarrativeStore` as the first argument
    delegates to the persistent regeneration path instead.
    """

    if args and not isinstance(args[0], NarrativeStore):
        event_input = args[0]
        config = kwargs.pop("config", None)
        model = kwargs.pop("model", None)
        if len(args) > 1 and model is None:
            if isinstance(args[1], NarrativeConfig):
                config = args[1]
            else:
                model = args[1]
        if len(args) > 2 and model is None:
            model = args[2]
        return _synthesize_event_list(
            event_input,
            config=config,
            model=model,
            now=kwargs.pop("now", None),
        )
    return regenerate(*args, **kwargs)


__all__ = [
    "KNOWN_SYNTHESIS_STATUSES",
    "build_chapters",
    "choose_bucket",
    "deterministic_chapter",
    "deterministic_entry",
    "parse_model_response",
    "regenerate",
    "regenerate_story",
    "synthesis_status_known",
    "synthesize",
]
