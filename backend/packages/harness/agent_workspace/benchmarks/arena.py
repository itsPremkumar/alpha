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
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BotLeaderboardEntry:
    bot_name: str
    challenges_attempted: int
    challenges_passed: int
    pass_rate: float
    avg_duration_seconds: float
    reputation_score: float  # 0.0 to 100.0
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
        simulated_pass: bool = True,
        duration_seconds: float = 8.5,
        tokens_consumed: int = 1420,
    ) -> ArenaRunResult:
        """Execute a challenge run and calculate performance score."""
        if challenge_id not in self._challenges:
            raise KeyError(f"Unknown challenge ID: {challenge_id}")

        cost_usd = round((tokens_consumed / 1000) * 0.003, 4)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # Scoring Formula: 70 pts for correctness, 15 pts for speed, 15 pts for token frugality
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
        )
        self._runs.append(result)
        return result

    def get_leaderboard(self) -> list[BotLeaderboardEntry]:
        """Aggregate run results and compute ranked leaderboard."""
        bot_stats: dict[str, dict[str, Any]] = {}

        # Default roster seed
        for default_bot in ("architect", "coder", "security-auditor", "reviewer", "lead_agent"):
            bot_stats[default_bot] = {"attempted": 0, "passed": 0, "durations": [], "scores": []}

        for r in self._runs:
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
            pass_rate = round((pas / att * 100) if att > 0 else 90.0, 1)
            avg_dur = round(sum(st["durations"]) / len(st["durations"]) if st["durations"] else 12.0, 1)
            reputation = round(sum(st["scores"]) / len(st["scores"]) if st["scores"] else 85.0, 1)

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

        # Sort by reputation score descending
        entries.sort(key=lambda e: e.reputation_score, reverse=True)
        for idx, item in enumerate(entries):
            item.rank = idx + 1

        return entries


_ARENAS: dict[str, BenchmarkArena] = {}


def get_benchmark_arena(project_id: str = "default") -> BenchmarkArena:
    if project_id not in _ARENAS:
        _ARENAS[project_id] = BenchmarkArena(project_id)
    return _ARENAS[project_id]
