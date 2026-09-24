"""Local zero-cost System 1 classifier — the DEFAULT engine when ``$JEV_API_KEY`` is absent.

Design: deterministic hashed lexical embeddings (camelCase/snake_case aware
tokenizer, crc32 feature hashing, sublinear term frequency, L2 norm) scored by
cosine similarity over numpy. No model download, no API key, zero tokens, tiny
RAM; the architecture design target is <15ms per decision on a laptop CPU
(<50ms worst case).

Evidence lexicons (hazard/benign/success/failure) score the ``context`` only —
never the question — so a question alone can never produce a positive or
dangerous decision (fail-closed by construction).

Optional ONNX acceleration lives behind a guarded import: it activates only
when a local model + vocab are configured via environment variables, and any
absence or runtime failure degrades honestly to the numpy path with a disclosed
``unavailable``/``degraded`` reason via
:meth:`LocalReflexClassifier.acceleration_status` — availability is never
implied, only reported.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import zlib
from collections.abc import Sequence
from typing import Any

import numpy as np

from alpha.system1.models import ENGINE_LOCAL, ChoiceResult, NoulResult, ScoreResult

logger = logging.getLogger(__name__)

DEFAULT_EMBED_DIM = 384
_CHOICE_TEMPERATURE = 0.1
_NOUL_BASE_WEIGHT = 2.2
_SCORE_NET_GAIN = 4.0
_HAZARD_PRESSURE = 1.5
_NEUTRAL_SCORE_CONFIDENCE = 0.15
_ONNX_MODEL_ENV = "ALPHA_SYSTEM1_ONNX_MODEL"
_ONNX_VOCAB_ENV = "ALPHA_SYSTEM1_ONNX_VOCAB"
_ONNX_MAX_TOKENS = 256

_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")

_STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have he her his i if in into is it "
    "its me my no not of on or our s she so such than that the their them then there these they this to too up us "
    "use very was we were what when where which who whom will with would you your".split()
)

# Substring hazard phrases over whitespace-normalized, lowercased context.
_HAZARD_PHRASES: tuple[tuple[str, float], ...] = (
    ("rm -rf", 0.9),
    ("rm -fr", 0.9),
    ("--no-preserve-root", 1.0),
    ("mkfs", 1.0),
    ("format c:", 1.0),
    ("dd if=", 1.0),
    ("/dev/sda", 0.9),
    ("drop table", 1.0),
    ("drop database", 1.0),
    ("git reset --hard", 0.6),
    ("git push --force", 0.6),
    ("git clean -", 0.5),
    ("del /f /s", 0.9),
    ("rmdir /s", 0.8),
    ("remove-item -recurse", 0.8),
    ("reg delete", 0.7),
    ("shutdown", 0.5),
    ("reboot", 0.4),
    ("kill -9", 0.5),
    ("chmod 777", 0.6),
    ("fork bomb", 1.0),
)

# Benign work vocab, keyed by the SAME stemmed tokens the embedder produces.
_BENIGN_WEIGHTS: dict[str, float] = {
    "read": 0.25,
    "list": 0.25,
    "print": 0.25,
    "show": 0.2,
    "describe": 0.25,
    "preview": 0.3,
    "diff": 0.35,
    "status": 0.25,
    "test": 0.3,
    "check": 0.25,
    "grep": 0.35,
    "search": 0.3,
    "summarize": 0.3,
    "explain": 0.3,
    "inspect": 0.3,
    "compile": 0.3,
    "pytest": 0.4,
    "unittest": 0.4,
    "typecheck": 0.35,
    "lint": 0.3,
}

_SUCCESS_PATTERNS: tuple[tuple[str, float], ...] = (
    (r"\b0\s+failed\b", 1.0),
    (r"\b0\s+errors?\b", 1.0),
    (r"\b\d+\s+passed\b", 0.7),
    (r"\ball\s+(?:tests?|checks?|steps?)\s+pass(?:ed|ing)?\b", 1.2),
    (r"\b(?:tests?|checks?|build)\s+pass(?:ed|ing)?\b", 0.8),
    (r"\bno\s+(?:errors?|failures?|failed)\b", 0.9),
    (r"\bexit\s+code\s*[:=]?\s*0\b", 1.0),
    (r"\bsuccess(?:fully|ful)?\b", 0.9),
    (r"\bsucceed(?:ed|s)?\b", 0.9),
    (r"\bobjective\s+(?:satisfied|met|complete[d]?)\b", 1.2),
    (r"\bcomplete[d]?\b", 0.5),
    (r"\bdone\b", 0.5),
    (r"\bpass(?:ed|ing)?\b", 0.4),
)

_FAILURE_PATTERNS: tuple[tuple[str, float], ...] = (
    (r"\b[1-9]\d*\s+failed\b", 0.9),
    (r"\bfailed\b", 0.6),
    (r"\bfail(?:ing|s)\b", 0.6),
    (r"\bfailure(?:s)?\b", 0.7),
    (r"\berrors?\b", 0.6),
    (r"\btraceback\b", 0.9),
    (r"\bassertion\s*error\b", 0.9),
    (r"\bassertionerror\b", 0.9),
    (r"\bexception\b", 0.6),
    (r"\bnon[- ]?zero\s+exit\b", 1.0),
    (r"\bcrash(?:ed|es)?\b", 0.8),
    (r"\bnot\s+(?:passing|satisfied|complete[d]?|done)\b", 1.0),
)

_SUCCESS_RE: tuple[tuple[re.Pattern[str], float], ...] = tuple((re.compile(pattern), weight) for pattern, weight in _SUCCESS_PATTERNS)
_FAILURE_RE: tuple[tuple[re.Pattern[str], float], ...] = tuple((re.compile(pattern), weight) for pattern, weight in _FAILURE_PATTERNS)
_HAZARD_NORM: tuple[tuple[str, float], ...] = tuple((_WS.sub(" ", phrase.lower()), weight) for phrase, weight in _HAZARD_PHRASES)


def tokenize(text: str) -> list[str]:
    """Lowercased, stopword-filtered, lightly stemmed tokens.

    Splits camelCase (``RunTests`` -> ``Run Tests``) and separators
    (``pytest_runner`` -> ``pytest runner``) before tokenizing, so candidate
    tool names embed into the same space as prose context.
    """
    if not text:
        return []
    spaced = _CAMEL_SPLIT.sub(" ", text)
    tokens: list[str] = []
    for word in _NON_ALNUM.sub(" ", spaced.lower()).split():
        if word in _STOPWORDS:
            continue
        tokens.append(_stem(word))
    return tokens


def _stem(token: str) -> str:
    """Deliberately light, deterministic stemmer (same rule on both sides)."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("ss"):
        return token
    if len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    if len(token) > 6 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 5 and token.endswith("ed"):
        token = token[:-2]
    return token


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _hazard_weight(text: str) -> tuple[float, int]:
    """(weight, hits) of destructive-phrase matches over normalized text."""
    weight = 0.0
    hits = 0
    for phrase, phrase_weight in _HAZARD_NORM:
        if phrase in text:
            weight += phrase_weight
            hits += 1
    return weight, hits


def _benign_weight(tokens: Sequence[str]) -> tuple[float, int]:
    """(weight, hits) of benign-work token matches (distinct tokens only)."""
    weight = 0.0
    hits = 0
    for token in set(tokens):
        token_weight = _BENIGN_WEIGHTS.get(token)
        if token_weight is not None:
            weight += token_weight
            hits += 1
    return weight, hits


def _signal_weight(text: str, lexicon: tuple[tuple[re.Pattern[str], float], ...]) -> float:
    """Sum of weights of matched patterns (each pattern counts at most once)."""
    return sum(weight for pattern, weight in lexicon if pattern.search(text))


def _try_load_onnx(model_path: str | None, vocab_path: str | None) -> tuple[Any, dict[str, int] | None, str]:
    """Best-effort ONNX session load behind an optional import.

    Returns ``(session, vocab, reason)``; on any failure the session is None
    and ``reason`` honestly states why the acceleration is unavailable.
    """
    if not model_path or not vocab_path:
        return None, None, (f"unavailable: no local ONNX model configured (set {_ONNX_MODEL_ENV} and {_ONNX_VOCAB_ENV}); using the numpy hashed-embedding CPU path")
    try:
        import onnxruntime
    except ImportError:
        return None, None, "unavailable: onnxruntime is not installed; using the numpy hashed-embedding CPU path"
    try:
        with open(vocab_path, encoding="utf-8") as handle:
            vocab = json.load(handle)
        if not isinstance(vocab, dict):
            raise ValueError(f"vocab file must be a JSON object mapping token -> id, got {type(vocab).__name__}")
        session = onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return session, {str(key): int(value) for key, value in vocab.items()}, "onnx"
    except Exception as exc:  # noqa: BLE001 - any load failure degrades honestly, never crashes the harness
        return None, None, f"unavailable: failed to load ONNX model ({type(exc).__name__}: {str(exc)[:200]})"


class LocalReflexClassifier:
    """Pure-CPU, zero-cost, deterministic System 1 classifier.

    Default path: hashed lexical embeddings + cosine similarity (numpy).
    Optional path: a local ONNX sentence-encoder when configured; failures
    degrade to the default path at runtime with a disclosed reason.
    """

    def __init__(
        self,
        *,
        dim: int = DEFAULT_EMBED_DIM,
        onnx_model_path: str | None = None,
        onnx_vocab_path: str | None = None,
    ) -> None:
        if dim < 8:
            raise ValueError(f"embed dim must be >= 8, got {dim}")
        self._dim = int(dim)
        # Bumped whenever the embedding backend changes at runtime (ONNX
        # degrade); cache keys must include it so vectors from two different
        # embedding spaces are never mixed.
        self.embed_epoch: int = 0
        model_path = onnx_model_path if onnx_model_path is not None else (os.environ.get(_ONNX_MODEL_ENV) or None)
        vocab_path = onnx_vocab_path if onnx_vocab_path is not None else (os.environ.get(_ONNX_VOCAB_ENV) or None)
        self._onnx_session: Any | None = None
        self._onnx_vocab: dict[str, int] | None = None
        self._onnx_session, self._onnx_vocab, self._onnx_reason = _try_load_onnx(model_path, vocab_path)

    def acceleration_status(self) -> dict[str, Any]:
        """Honest disclosure of the embedding backend and ONNX availability."""
        return {
            "backend": "onnx" if self._onnx_session is not None else "numpy_hash",
            "onnx_available": self._onnx_session is not None,
            "onnx_reason": self._onnx_reason,
            "embed_dim": self._dim,
        }

    def embed(self, text: str) -> np.ndarray:
        """L2-normalized embedding of ``text`` (ONNX path when active)."""
        if self._onnx_session is not None:
            try:
                return self._onnx_embed(text)
            except Exception as exc:  # noqa: BLE001 - degrade honestly once, then stay on numpy
                self._onnx_session = None
                self._onnx_vocab = None
                self.embed_epoch += 1
                self._onnx_reason = f"degraded: onnx inference failed at runtime ({type(exc).__name__}: {str(exc)[:200]}); using numpy path"
                logger.warning("System 1 ONNX embed failed, degrading to numpy path: %s", exc)
        return self._numpy_embed(text)

    def similarity(self, left: np.ndarray, right: np.ndarray) -> float:
        """Cosine similarity (both vectors are L2-normalized by :meth:`embed`)."""
        return float(np.dot(left, right))

    def predict_choice(self, context: str, candidates: Sequence[str]) -> ChoiceResult:
        """Softmax distribution over candidate embeddings; winner = argmax."""
        context_vec = self.embed(context)
        similarities = np.asarray(
            [self.similarity(context_vec, self.embed(candidate)) for candidate in candidates],
            dtype=np.float64,
        )
        logits = similarities / _CHOICE_TEMPERATURE
        shifted = np.exp(logits - logits.max())
        probabilities = shifted / shifted.sum()
        winner = candidates[int(np.argmax(probabilities))]
        return ChoiceResult(
            winner=winner,
            probabilities={str(candidate): float(probability) for candidate, probability in zip(candidates, probabilities, strict=True)},
            engine=ENGINE_LOCAL,
        )

    def predict_score(self, context: str, *, question: str = "", scale: tuple[float, float] = (0.0, 1.0)) -> ScoreResult:
        """Ordered score from hazard/benign evidence in ``context``.

        ``question`` is carried for provenance/transport parity only: local
        evidence is scored from the context, so a framing question can never
        move the score on its own. No signal -> neutral 0.5 with low confidence.
        """
        del question  # provenance-only by contract (documented above)
        lo, hi = scale
        normalized = _WS.sub(" ", context).lower()
        hazard, hazard_hits = _hazard_weight(normalized)
        benign, benign_hits = _benign_weight(tokenize(context))
        if hazard_hits == 0 and benign_hits == 0:
            confidence = _NEUTRAL_SCORE_CONFIDENCE
        else:
            confidence = min(0.95, 0.3 + 0.5 * min(1.0, hazard + benign))
        raw = _sigmoid(_SCORE_NET_GAIN * (benign - _HAZARD_PRESSURE * hazard))
        return ScoreResult(score=lo + (hi - lo) * raw, confidence=confidence, scale=scale, engine=ENGINE_LOCAL)

    def predict_noul(self, context: str, question: str, *, threshold: float = 0.5) -> NoulResult:
        """Strict yes/no gate over success/failure evidence in ``context``.

        The question frames the decision but contributes NO evidence: an
        evidence-free context yields P(yes)=0.5, which fails the strict
        ``> threshold`` comparison and closes on ``decision=False`` (fail-closed).
        ``probability`` is the probability OF the returned decision.
        """
        del question  # provenance-only by contract (documented above)
        normalized = _WS.sub(" ", context).lower()
        success = _signal_weight(normalized, _SUCCESS_RE)
        failure = _signal_weight(normalized, _FAILURE_RE)
        p_yes = _sigmoid(_NOUL_BASE_WEIGHT * (success - failure))
        decision = p_yes > threshold
        probability = p_yes if decision else 1.0 - p_yes
        return NoulResult(decision=decision, probability=probability, threshold=threshold, engine=ENGINE_LOCAL)

    def _numpy_embed(self, text: str) -> np.ndarray:
        tokens = tokenize(text)
        if not tokens:
            return np.zeros(self._dim, dtype=np.float32)
        weights: dict[str, float] = {}
        for index, token in enumerate(tokens):
            weights[token] = weights.get(token, 0.0) + 1.0
            if index + 1 < len(tokens):
                bigram = f"{token} {tokens[index + 1]}"
                weights[bigram] = weights.get(bigram, 0.0) + 0.5
        items = list(weights.items())
        indices = np.array([zlib.crc32(name.encode("utf-8")) % self._dim for name, _ in items], dtype=np.int64)
        values = np.array([1.0 + math.log(weight) for _, weight in items], dtype=np.float64)
        vector = np.bincount(indices, weights=values, minlength=self._dim)
        norm = math.sqrt(float(vector @ vector))
        if norm > 0.0:
            vector /= norm
        return vector.astype(np.float32)

    def _onnx_embed(self, text: str) -> np.ndarray:
        """Mean-pooled embedding through the session (sentence-transformers layout)."""
        if self._onnx_session is None or self._onnx_vocab is None:
            raise RuntimeError("ONNX session is not configured")
        tokens = tokenize(text)[:_ONNX_MAX_TOKENS] or ["[unused]"]
        unk_id = int(self._onnx_vocab.get("[UNK]", 0))
        ids = [int(self._onnx_vocab.get(token, unk_id)) for token in tokens]
        outputs = self._onnx_session.run(None, {"input_ids": np.asarray([ids], dtype=np.int64)})
        hidden = np.asarray(outputs[0], dtype=np.float32)
        vector = hidden.mean(axis=1)[0]
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector = vector / norm
        return vector.astype(np.float32)
