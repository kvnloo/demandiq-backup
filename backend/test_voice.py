"""voice.py: only well-formed values reach the form, whatever the model says. Offline (model stubbed)."""

import voice


def test_clean_keeps_good_values_and_drops_bad_ones():
    out = voice.clean({
        "name": "  Main Street   Corridor Study ",
        "client": "City of Fresno",
        "projectNumber": 30901249.003,
        "analyst": "a.k.",
        "checker": "Jonathan Smith",          # not initials -> dropped
        "date": "2026-11-10",
        "scenario": "null",                   # model's way of saying nothing -> dropped
        "projectType": "corridor",
        "analysisPeriods": ["pm peak", "AM", "midday"],
        "extra": "ignored",
    })
    assert out == {
        "name": "Main Street Corridor Study",
        "client": "City of Fresno",
        "projectNumber": "30901249.003",
        "analyst": "AK",
        "date": "2026-11-10",
        "projectType": "Corridor",
        "analysisPeriods": ["AM", "PM"],
    }


def test_clean_rejects_wrong_shapes():
    assert voice.clean({"date": "next Tuesday", "projectType": "Network", "analysisPeriods": [],
                        "name": "x" * 500, "client": ["a"], "analyst": True}) == {}


def test_understand_reads_json_even_when_wrapped_and_survives_junk():
    real = voice._ask
    try:
        voice._ask = lambda text, today: 'Sure!\n```json\n{"client": "Caltrans", "analysisPeriods": "PM"}\n```'
        assert voice.understand("PM peak study for Caltrans") == {"client": "Caltrans", "analysisPeriods": ["PM"]}
        voice._ask = lambda text, today: "I could not find anything."
        assert voice.understand("hmm") == {}
        assert voice.understand("   ") == {}       # nothing said: no model call needed
        voice._ask = lambda text, today: '{"date": "2026-11-09", "client": "Caltrans"}'
        assert voice.understand("study for Caltrans") == {"client": "Caltrans"}   # no date said -> not kept
        assert voice.understand("Caltrans, counts from Tuesday")["date"] == "2026-11-09"
    finally:
        voice._ask = real


if __name__ == "__main__":       # run_tests.sh runs each file as a script
    test_clean_keeps_good_values_and_drops_bad_ones()
    test_clean_rejects_wrong_shapes()
    test_understand_reads_json_even_when_wrapped_and_survives_junk()
    print("test_voice: 3 passed")
