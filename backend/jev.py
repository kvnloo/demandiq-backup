"""Jev (TypeSafe System One) for one judgment: which project intersection a count
file's intersection name means, when the token matcher in the UI can't tell.

Jev only SELECTS among the project's own intersection names (or "none of these")
and only SUGGESTS: the engineer still clicks Apply, so a wrong pick never moves
volumes on its own. Same pattern as CrashIQ's geocode leftovers (crashiq/jev.py).

Key: TYPESAFE_API_KEY in the environment or ~/.typesafe/credentials, server-side
only. No key, no SDK, or Jev down → no suggestion; the manual dropdown works as before.
Pricing (docs.typesafe.ai/models, 2026-10-18): $0.042 per million input tokens,
~430 tokens per name, so about $0.00002 a call.
"""

import os
from functools import lru_cache
from pathlib import Path

MODEL = "jev-1.13.0"          # pinned so the same name gives the same pick
NONE = "none of these"
# shortcut: set from an 8-case probe (7/8 right; the miss — "Alvarado Blvd" vs
# "Alvarado-Niles Rd" — came back p 0.81), not a labeled test set. Recalibrate on
# the capture log's engineer-confirmed matches once there are ~100.
MIN_P = 0.90
MIN_MARGIN = 0.50
MAX_ITEMS = 50                # per request; a project has a handful of intersections
INSTRUCTIONS = (
    "A traffic count file names the intersection in `count.name`. Vendors abbreviate, swap the street "
    "order, use route numbers or old names, and add city, direction or period notes. Which listed project "
    "intersection is it? If none fits, answer 'none of these'. Do not guess between two that fit equally."
)


def _api_key():
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    f = Path.home() / ".typesafe" / "credentials"
    if f.is_file():
        for line in f.read_text().splitlines():
            if line.strip().startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def available():
    try:
        import typesafe_sdk  # noqa: F401
    except ImportError:
        return False
    return _api_key() is not None


@lru_cache(maxsize=2048)
def _ask(name, labels):
    """(choice | None, p, p_runner_up) from Jev, cached per (name, candidate set)."""
    from typesafe_sdk import Choice, TypeSafeClient
    with TypeSafeClient(api_key=_api_key()) as client:
        r = client.system_one(
            state={"count": {"name": name}},
            questions={"pick": Choice(instructions=INSTRUCTIONS,
                                      criteria={**{l: None for l in labels}, NONE: None})},
            model=MODEL,
        )
    a = r.answers["pick"]
    probs = a.probabilities or {a.choice: float(a.confidence or 0.0)}
    ranked = sorted(probs.values(), reverse=True) + [0.0]
    return (None if a.choice == NONE else a.choice), float(probs.get(a.choice, ranked[0])), float(ranked[1])


def accept(choice, p, p2, labels):
    """Seam check (code, not Jev): the pick must be one of ours and clearly ahead."""
    return choice in labels and p >= MIN_P and (p - p2) >= MIN_MARGIN


def suggest(names, candidates):
    """names: [str]; candidates: [str] (project intersection names).
    Returns [{choice, p} | None] aligned with names. Never raises for Jev trouble."""
    labels = tuple(dict.fromkeys(c for c in candidates if c))
    out = []
    for name in names[:MAX_ITEMS]:
        if not name or not labels:
            out.append(None)
            continue
        try:
            choice, p, p2 = _ask(str(name), labels)
        except Exception:
            out.append(None)              # Jev down / quota → manual, as today
            continue
        out.append({"choice": choice, "p": round(p, 3)} if accept(choice, p, p2, labels) else None)
    return out + [None] * (len(names) - len(out))
