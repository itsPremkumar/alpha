"""SWE-Bench Evaluation Arena & Autonomous Bot Leaderboard.

Provides standardized coding and architectural challenges to benchmark bot competence,
latency, and token efficiency. Directly feeds bot performance metrics back into
the Task Auction Matchmaker to calibrate dynamic bidding reputation scores.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkChallenge:
    challenge_id: str
    title: str
    category: str  # "bugfix", "refactor", "optimization", "security"
    difficulty: str  # "easy", "medium", "hard"
    description: str
    verification_suite: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArenaRunResult:
    run_id: str
    challenge_id: str
    bot_name: str
    passed: bool
    duration_seconds: float
    tokens_consumed: int
    cost_usd: float
    score: float  # 0.0 to 100.0
    executed_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    # Defaults to "simulated", never "measured": without a wired challenge
    # executor the inputs are caller-asserted, so the run must not be treated
    # as a measurement. Only runs explicitly recorded as "measured" by a real
    # executor feed the leaderboard aggregates.
    evidence_kind: Literal["simulated", "measured"] = "simulated"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BotLeaderboardEntry:
    bot_name: str
    challenges_attempted: int
    challenges_passed: int
    # None (never an invented default) while the bot has no measured attempts.
    pass_rate: float | None
    avg_duration_seconds: float | None
    reputation_score: float | None  # 0.0 to 100.0; None when unmeasured
    rank: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkArena:
    """Evaluates specialist bots across coding benchmarks and tracks fleet leaderboard rankings."""

    STANDARD_CHALLENGES = [
        BenchmarkChallenge(
            challenge_id="swe-01-null-guard",
            title="Fix Null Pointer in Authentication Token Parser",
            category="bugfix",
            difficulty="easy",
            description="Prevent Unhandled NullReferenceException when Authorization header contains malformed token structure.",
            verification_suite="pytest tests/test_auth_tokens.py -k test_malformed_token_handling",
        ),
        BenchmarkChallenge(
            challenge_id="swe-02-concurrency-lock",
            title="Resolve File Lock Deadlock in Parallel Worktrees",
            category="refactor",
            difficulty="medium",
            description="Implement two-phase lock acquisition with ordered hierarchy to prevent circular wait condition.",
            verification_suite="pytest tests/test_locks.py -k test_concurrent_lock_hierarchy",
        ),
        BenchmarkChallenge(
            challenge_id="swe-03-rate-limiter",
            title="Implement Token Bucket Rate Limiter with Redis Backing",
            category="optimization",
            difficulty="hard",
            description="Construct leaky bucket rate limiter supporting 10k RPS burst capacity with atomic Lua scripts.",
            verification_suite="pytest tests/test_rate_limiter.py -k test_burst_capacity",
        ),
    ]

    def __init__(self, project_id: str = "default"):
        self.project_id = project_id
        self._challenges = {c.challenge_id: c for c in self.STANDARD_CHALLENGES}
        self._runs: list[ArenaRunResult] = []

    def list_challenges(self) -> list[BenchmarkChallenge]:
        return list(self._challenges.values())

    def run_challenge(
        self,
        challenge_id: str,
        bot_name: str = "coder",
        *,
        simulated_pass: bool,
        duration_seconds: float,
        tokens_consumed: int,
        evidence_kind: Literal["simulated", "measured"] = "simulated",
    ) -> ArenaRunResult:
        """Record a challenge run from caller-supplied inputs.

        Pass/duration/token inputs are REQUIRED keyword arguments — there are
        no fabricated defaults, so a run receipt can never be invented for a
        call that supplied nothing. With no challenge executor wired, runs
        default to evidence_kind="simulated"; only runs recorded as
        "measured" (by a real executor) are counted in the leaderboard.
        """
        if challenge_id not in self._challenges:
            raise KeyError(f"Unknown challenge ID: {challenge_id}")
        if evidence_kind not in ("simulated", "measured"):
            raise ValueError(f"evidence_kind must be 'simulated' or 'measured', got {evidence_kind!r}")

        cost_usd = round((tokens_consumed / 1000) * 0.003, 4)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # Scoring Formula: 70 pts for correctness, 15 pts for speed, 15 pts for token frugality
        # (scored strictly from the caller-supplied inputs above)
        base_correctness = 70.0 if simulated_pass else 10.0
        speed_bonus = max(0.0, 15.0 - (duration_seconds / 2.0))
        token_bonus = max(0.0, 15.0 - (tokens_consumed / 500.0))
        total_score = round(base_correctness + speed_bonus + token_bonus, 1)

        result = ArenaRunResult(
            run_id=run_id,
            challenge_id=challenge_id,
            bot_name=bot_name,
            passed=simulated_pass,
            duration_seconds=duration_seconds,
            tokens_consumed=tokens_consumed,
            cost_usd=cost_usd,
            score=min(100.0, max(0.0, total_score)),
            evidence_kind=evidence_kind,
        )
        self._runs.append(result)
        return result

    def get_leaderboard(self) -> list[BotLeaderboardEntry]:
        """Aggregate measured run results and compute the ranked leaderboard.

        Runs that are not evidence_kind="measured" are excluded from every
        aggregate, and bots with zero measured attempts report None for
        pass rate, duration, and reputation instead of invented defaults.
        """
        bot_stats: dict[str, dict[str, Any]] = {}

        # Default roster seed
        for default_bot in ("architect", "coder", "security-auditor", "reviewer", "lead_agent"):
            bot_stats[default_bot] = {"attempted": 0, "passed": 0, "durations": [], "scores": []}

        for r in self._runs:
            if r.evidence_kind != "measured":
                # Simulated runs stay on record as runs, but never feed
                # leaderboard aggregates.
                continue
            if r.bot_name not in bot_stats:
                bot_stats[r.bot_name] = {"attempted": 0, "passed": 0, "durations": [], "scores": []}
            st = bot_stats[r.bot_name]
            st["attempted"] += 1
            if r.passed:
                st["passed"] += 1
            st["durations"].append(r.duration_seconds)
            st["scores"].append(r.score)

        entries: list[BotLeaderboardEntry] = []
        for bname, st in bot_stats.items():
            att = st["attempted"]
            pas = st["passed"]
            pass_rate = round((pas / att * 100), 1) if att > 0 else None
            avg_dur = round(sum(st["durations"]) / len(st["durations"]), 1) if st["durations"] else None
            reputation = round(sum(st["scores"]) / len(st["scores"]), 1) if st["scores"] else None

            entries.append(
                BotLeaderboardEntry(
                    bot_name=bname,
                    challenges_attempted=att,
                    challenges_passed=pas,
                    pass_rate=pass_rate,
                    avg_duration_seconds=avg_dur,
                    reputation_score=reputation,
                )
            )

        # Sort by measured reputation descending; bots without measured data
        # (None) sort last instead of comparing None against floats.
        entries.sort(key=lambda e: (e.reputation_score is not None, e.reputation_score or 0.0), reverse=True)
        for idx, item in enumerate(entries):
            item.rank = idx + 1

        return entries


_ARENAS: dict[str, BenchmarkArena] = {}


def get_benchmark_arena(project_id: str = "default") -> BenchmarkArena:
    if project_id not in _ARENAS:
        _ARENAS[project_id] = BenchmarkArena(project_id)
    return _ARENAS[project_id]
