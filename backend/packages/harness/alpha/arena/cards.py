"""The strategy card deck and the dealer.

Every competitor gets the same task and exactly one card: one
reasoning mode, one workflow, one strategy. The shipped deck is
15 x 12 x 12 = 2,160 distinct cards.

The dealer guarantees, for a given seed and deck:

* **No card is dealt twice.**
* **Every part is dealt as evenly as possible** - any two counts
  for the same dimension differ by at most 1 - so a 100-agent
  arena uses every reasoning mode, every workflow and every
  strategy.
* **Up to 144 agents, no two agents even share two of their three
  parts.** Any two competitors differ in at least two of the
  three, so every attack comes from a genuinely different angle.

The construction is deterministic for a seed: agent slot ``i``
gets reasoning ``i mod R`` and workflow ``(i + i // lcm(R, W)) mod W``,
which never repeats a workflow inside a reasoning mode and stays
balanced. The strategies are then a balanced *proper edge colouring*
of that reasoning x workflow grid (Konig: every bipartite graph
has an edge colouring with as many colours as its maximum degree),
balanced by de Werra's alternating-path swaps until each colour is
used floor or ceil of ``n / k`` times. The seed shuffles every
label list and who gets which card.

Past 144 agents a repeated pair is unavoidable, so the dealer falls
back to a greedy assignment that still never repeats a full card and
stays as balanced and pair-sparse as it can.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from alpha.arena.models import CardPart, CardPartKind, StrategyCard

DEFAULT_DECK_PATH = Path(__file__).parent / "strategies.json"

# The shipped deck dimensions. The 144 boundary below is derived
# from the workflow count, not hardcoded: ceil(n / W) <= S requires
# n <= R * S when R <= W <= S... precisely, deg_w = ceil(n / W) must
# stay <= S, so n <= W * S = 12 * 12 = 144 with the shipped deck.


class DeckError(ValueError):
    """Raised when a strategy deck is malformed or cannot deal."""


def load_deck(path: Path | str = DEFAULT_DECK_PATH) -> dict[str, list[dict[str, str]]]:
    """Load and validate a strategy deck.

    Fails loudly on a bad deck: a duplicate id or an entry missing
    its ``name`` or ``how`` is a ``DeckError``, never a silent
    fallback to a partial deck.
    """
    deck_path = Path(path)
    with open(deck_path, encoding="utf-8") as handle:
        data = json.load(handle)
    validate_deck(data)
    return data


def validate_deck(data: dict[str, Any]) -> None:
    """Validate a deck dict, collecting every problem at once."""
    errors: list[str] = []
    for key in ("reasoning", "workflows", "strategies"):
        items = data.get(key)
        if not isinstance(items, list) or not items:
            errors.append(f"deck has no '{key}' entries")
            continue
        ids = [str(item.get("id")) for item in items]
        if len(set(ids)) != len(ids):
            errors.append(f"deck has a duplicate id in '{key}'")
        for item in items:
            if not str(item.get("name", "")).strip() or not str(item.get("how", "")).strip():
                errors.append(f"deck entry '{item.get('id')}' in '{key}' needs a name and a how")
    if errors:
        raise DeckError("; ".join(sorted(set(errors))))


def combo_count(data: dict[str, Any]) -> int:
    """The number of distinct cards a deck can deal."""
    return len(data["reasoning"]) * len(data["workflows"]) * len(data["strategies"])


def _edge_colour(edges: list[tuple[int, int]], k: int) -> list[int]:
    """Properly edge-colour a bipartite (multi)graph with ``k`` colours, then balance it.

    ``edges`` are ``(row, col)`` pairs and ``k`` must be at least the
    largest degree. Proper means no two edges at the same row, or at
    the same column, share a colour. Then de Werra's swaps even the
    colour classes out until each is used floor or ceil of
    ``len(edges) / k`` times.
    """
    colour: list[int | None] = [None] * len(edges)
    at: tuple[dict[int, dict[int, int]], dict[int, dict[int, int]]] = ({}, {})

    def slot(side: int, node: int) -> dict[int, int]:
        return at[side].setdefault(node, {})

    def put(edge: int, c: int) -> None:
        u, v = edges[edge]
        colour[edge] = c
        slot(0, u)[c] = edge
        slot(1, v)[c] = edge

    def take(edge: int) -> None:
        u, v = edges[edge]
        del slot(0, u)[colour[edge]]  # type: ignore[index]
        del slot(1, v)[colour[edge]]  # type: ignore[index]

    def walk(side: int, node: int, first: int, other: int) -> list[int]:
        path: list[int] = []
        c = first
        while True:
            edge = slot(side, node).get(c)
            if edge is None or edge in path:
                return path
            path.append(edge)
            node = edges[edge][1 - side]
            side = 1 - side
            c = other if c == first else first

    def swap(path: list[int], a: int, b: int) -> None:
        new = {edge: (b if colour[edge] == a else a) for edge in path}
        for edge in path:
            take(edge)
        for edge in path:
            put(edge, new[edge])

    for edge, (u, v) in enumerate(edges):
        free_u = [c for c in range(k) if c not in slot(0, u)]
        free_v = {c for c in range(k) if c not in slot(1, v)}
        c = next((c for c in free_u if c in free_v), None)
        if c is None:
            a, b = free_u[0], min(free_v)
            swap(walk(1, v, a, b), a, b)
            c = a
        put(edge, c)

    while True:
        count = [0] * k
        for c in colour:
            count[c] += 1
        hi = max(range(k), key=lambda c: (count[c], -c))
        lo = min(range(k), key=lambda c: (count[c], c))
        if count[hi] - count[lo] <= 1:
            return [c for c in colour if c is not None]
        swapped = False
        for side in (0, 1):
            for node in sorted(at[side]):
                s = at[side][node]
                if hi in s and lo not in s:
                    path = walk(side, node, hi, lo)
                    if sum(1 for e in path if colour[e] == hi) > sum(1 for e in path if colour[e] == lo):
                        swap(path, hi, lo)
                        swapped = True
                        break
            if swapped:
                break
        if not swapped:
            raise DeckError("could not balance the strategy cards")


def deal(n: int, seed: str | int, data: dict[str, Any]) -> list[tuple[int, int, int]]:
    """Deal ``n`` distinct ``(reasoning, workflow, strategy)`` index triples.

    Deterministic for a given seed and deck. See the module docstring
    for the guarantees. Raises ``DeckError`` when ``n`` is outside
    ``1 .. combo_count(data)``.
    """
    r_count, w_count, s_count = len(data["reasoning"]), len(data["workflows"]), len(data["strategies"])
    total = r_count * w_count * s_count
    if n < 1 or n > total:
        raise DeckError(f"can deal between 1 and {total} cards, not {n}")
    rng = random.Random(f"alpha.arena.deal:{seed}")
    pr, pw, ps = list(range(r_count)), list(range(w_count)), list(range(s_count))
    rng.shuffle(pr)
    rng.shuffle(pw)
    rng.shuffle(ps)
    lcm = r_count * w_count // math.gcd(r_count, w_count)
    cells = [(i % r_count, (i + i // lcm) % w_count) for i in range(n)]
    deg_r = max(sum(1 for r, _ in cells if r == x) for x in range(r_count))
    deg_w = max(sum(1 for _, w in cells if w == x) for x in range(w_count))
    if deg_r <= s_count and deg_w <= s_count:
        order = list(range(n))
        rng.shuffle(order)
        colours = _edge_colour([cells[i] for i in order], s_count)
        strategy_of = {order[j]: colours[j] for j in range(n)}
        triples = [(r, w, strategy_of[i]) for i, (r, w) in enumerate(cells)]
    else:
        # Past that size a repeated pair is unavoidable. Stay balanced
        # and never repeat a card.
        count = [0] * s_count
        pairs_rs: dict[tuple[int, int], int] = {}
        pairs_ws: dict[tuple[int, int], int] = {}
        used: set[tuple[int, int, int]] = set()
        triples = []
        for r, w in cells:
            low = min(count)
            s = min(
                (c for c in range(s_count) if (r, w, c) not in used),
                key=lambda c: (count[c] - low, pairs_rs.get((r, c), 0), pairs_ws.get((w, c), 0), c),
            )
            used.add((r, w, s))
            count[s] += 1
            pairs_rs[(r, s)] = pairs_rs.get((r, s), 0) + 1
            pairs_ws[(w, s)] = pairs_ws.get((w, s), 0) + 1
            triples.append((r, w, s))
    dealt = [(pr[r], pw[w], ps[s]) for r, w, s in triples]
    rng.shuffle(dealt)
    return dealt


def deal_cards(n: int, seed: str | int, data: dict[str, Any]) -> list[StrategyCard]:
    """Deal ``n`` concrete :class:`StrategyCard` objects."""
    return [
        StrategyCard(
            reasoning=CardPart(**data["reasoning"][r]),
            workflow=CardPart(**data["workflows"][w]),
            strategy=CardPart(**data["strategies"][s]),
        )
        for r, w, s in deal(n, seed, data)
    ]


def card_line(card: StrategyCard) -> str:
    """One-line card summary."""
    return card.line


def part_name(card: StrategyCard, kind: CardPartKind) -> str:
    """The name of one dimension of a card."""
    return getattr(card, kind.value).name


__all__ = [
    "DeckError",
    "DEFAULT_DECK_PATH",
    "card_line",
    "combo_count",
    "deal",
    "deal_cards",
    "load_deck",
    "part_name",
    "validate_deck",
]
