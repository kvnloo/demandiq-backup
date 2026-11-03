"""jev.py seam check + fallbacks — offline (Jev itself is stubbed)."""

import jev


def test_accept_requires_listed_pick_and_clear_lead():
    labels = ("A St & 1st Ave", "B St & 2nd Ave")
    assert jev.accept("A St & 1st Ave", 0.95, 0.03, labels)
    assert not jev.accept("Made Up St & 9th", 0.99, 0.0, labels)    # not ours → never
    assert not jev.accept("A St & 1st Ave", 0.81, 0.18, labels)     # the probe's trap case
    assert not jev.accept(None, 0.97, 0.01, labels)                  # "none of these"


def test_suggest_keeps_order_and_survives_jev_failure():
    def fake(name, labels):
        if name == "boom":
            raise RuntimeError("jev down")
        return {"a/1": ("A St & 1st Ave", 0.97, 0.02), "x": (None, 0.95, 0.03)}[name]
    real, jev._ask = jev._ask, fake
    try:
        out = jev.suggest(["a/1", "boom", "x", ""], ["A St & 1st Ave", "B St & 2nd Ave"])
    finally:
        jev._ask = real
    assert out == [{"choice": "A St & 1st Ave", "p": 0.97}, None, None, None]


if __name__ == "__main__":       # run_tests.sh runs each file as a script
    test_accept_requires_listed_pick_and_clear_lead()
    test_suggest_keeps_order_and_survives_jev_failure()
    print("test_jev: 2 passed")
