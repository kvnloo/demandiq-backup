"""Live known-answer eval: count_agent.extract on every sample vs expected.json.

    cd backend && ANTHROPIC_API_KEY=... python tests/fixtures/samples/check_extraction.py

Costs API tokens (8 calls), so it is not in CI. Prints, per file, how many of
the expected fields the LLM extraction got exactly (volumes, peak starts) or
within tolerance (PHF ±0.01, HV% ±0.2).
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from count_agent import extract  # noqa: E402


def score(got, want):
    hit = n = 0
    for p in ("AM", "PM"):
        for mv, v in want["volumes"][p].items():
            n += 1
            hit += (got.get("volumes") or {}).get(p, {}).get(mv) == v
        for key, tol in (("phf", 0.01), ("hv_pct", 0.2)):
            w, g = want[key][p], (got.get(key) or {}).get(p)
            n += 1
            hit += (w is None and g is None) or (w is not None and g is not None and abs(g - w) <= tol)
        n += 1
        hit += got.get(f"{p.lower()}_peak_start") == want[f"{p.lower()}_peak_start"]
    return hit, n


def main():
    key = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("ANTHROPIC_API_KEY not set")
    files = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))["files"]
    total = [0, 0]
    for name, exp in files.items():
        got = extract(str(HERE / name), key)["intersections"][0]
        hit, n = score(got, exp["intersections"][0])
        total[0] += hit
        total[1] += n
        print(f"{name:20} {hit}/{n} fields matched")
    print(f"ALL {total[0]}/{total[1]}")
    return 0 if total[0] == total[1] else 1


if __name__ == "__main__":
    sys.exit(main())
