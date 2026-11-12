"""Known-answer checks on the synthetic samples (tests/fixtures/samples).

Every third-party format DemandIQ reads has a synthetic look-alike made by
make_samples.py; the answers come from the generator's own numbers, so every
check below is exact. Runs offline (no API): the deterministic readers
(count_agent's file readers, count_verifier) and the report arithmetic.
The LLM extraction itself is checked against the same files with
`python tests/fixtures/samples/check_extraction.py` (needs ANTHROPIC_API_KEY).
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "tests" / "fixtures" / "samples"
sys.path[:0] = [str(HERE), str(HERE / "tools"), str(SAMPLES)]
os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-not-used-offline")

import count_agent  # noqa: E402
import count_verifier  # noqa: E402
import make_samples as M  # noqa: E402

EXP = json.loads((SAMPLES / "expected.json").read_text(encoding="utf-8"))
FILES = EXP["files"]
REPORT = []          # (file, fields checked) — printed per format at the end


def _ans(name):
    return FILES[name]["intersections"][0]


def _peak(totals, width):
    sums = [sum(totals[i:i + width]) for i in range(len(totals) - width + 1)]
    return sums.index(max(sums))


def test_expected_is_reproducible():
    assert M.expected() == FILES, "make_samples.py no longer reproduces expected.json"


def test_tabular_xlsx_verifier_and_formulas():
    from openpyxl import load_workbook
    for name in (n for n in FILES if n.endswith(".xlsx")):
        path, a = SAMPLES / name, _ans(name)
        rep = count_verifier.verify(str(path), FILES[name])["intersections"][0]
        assert rep["overall"] == "VERIFIED", (name, rep)
        n = sum(d["status"] == "VERIFIED" for p in rep["movements"].values() for d in p.values())
        assert n == 32 and all(rep["phf"][p]["python"] == a["phf"][p] for p in ("AM", "PM")), rep["phf"]
        text, ftype, warn = count_agent.read_file(str(path))
        assert ftype == "excel" and not warn and "PEAK HR VOL :" in text and a["name"] in text
        # HV% = (Total - Cars) / Total over the peak hour, read off the two sheets' PEAK HR VOL rows
        wb = load_workbook(path, read_only=True, data_only=True)
        vol = {s: [r for r in wb[s].iter_rows(values_only=True) if r[0] == "PEAK HR VOL :"] for s in ("Data - Total", "Data - Cars")}
        for i, p in enumerate(("AM", "PM")):
            tot, cars = vol["Data - Total"][i][17], vol["Data - Cars"][i][17]
            assert round(100 * (tot - cars) / tot, 1) == a["hv_pct"][p]
            assert dict(zip(M.T_MV, vol["Data - Total"][i][1:17])) == {m: a["volumes"][p][m] for m in M.T_MV}
        REPORT.append((name, "32 movements + 2 PHF verified by count_verifier, HV% by formula"))


def test_tabular_pdf_text():
    for name in (n for n in FILES if n.startswith("tabular15") and n.endswith(".pdf")):
        a = _ans(name)
        text, warn = count_agent._read_pdf_text(str(SAMPLES / name))
        page1 = text.split("=== PAGE 2 ===")[0]
        vols = [ln.split(":", 1)[1].split() for ln in page1.splitlines() if "PEAK HR VOL :" in ln]
        phfs = [ln.split(":", 1)[1].split() for ln in page1.splitlines() if "PEAK HR FACTOR :" in ln]
        starts = re.findall(r"PEAK HR : (\d\d:\d\d [AP]M)", page1)
        assert len(vols) == len(phfs) == len(starts) == 2 and not warn
        for i, p in enumerate(("AM", "PM")):
            assert dict(zip(M.T_MV, map(int, vols[i][:16]))) == {m: a["volumes"][p][m] for m in M.T_MV}
            assert int(vols[i][16]) == sum(a["volumes"][p].values())
            assert float(phfs[i][16]) == a["phf"][p]
            h, mnt = starts[i][:5].split(":")
            assert f"{int(h) % 12 + (12 if starts[i].endswith('PM') else 0):02d}:{mnt}" == a[f"{p.lower()}_peak_start"]
        REPORT.append((name, "32 movements + 2 PHF + 2 peak starts read from PDF text"))


def _rolling_from_rows(rows):
    """[(time, [16 movements L,T,R,U per NB,SB,EB,WB], total, hourly)] -> derived answer."""
    tot = [r[2] for r in rows]
    for i, r in enumerate(rows):
        assert r[2] == sum(r[1]), "interval total"
        if i >= 11:
            assert r[3] == sum(tot[i - 11:i + 1]), "rolling hourly total"
    k = _peak(tot, 12)
    j = k + _peak(tot[k:k + 12], 3)
    vol = {a + t: sum(rows[i][1][n * 4 + "LTRU".index(t)] for i in range(k, k + 12))
           for n, a in enumerate(M.APPR) for t in "LTRU"}
    return rows[k][0], vol, round(sum(tot[k:k + 12]) / (4 * sum(tot[j:j + 3])), 2), (k, j)


def _clock24(s):
    h, rest = s.split(":")
    return f"{int(h) % 12 + (12 if rest.endswith('PM') else 0):02d}:{rest[:2]}"


def test_rolling_csv_formulas():
    for name in (n for n in FILES if n.endswith(".csv")):
        a = _ans(name)
        p = "AM" if a["am_peak_start"] else "PM"
        rows = list(csv.reader(open(SAMPLES / name, encoding="utf-8-sig")))
        head = {r[0]: r[1] for r in rows if r and r[0]}
        i0 = next(i for i, r in enumerate(rows) if r[0] == "ALL-VEHICLE VOLUMES") + 2
        table = []
        for r in rows[i0:i0 + 24]:
            mv = [int(r[1 + n * 5 + c]) for n in range(4) for c in range(4)]   # L,T,R,U per approach (RTOR skipped)
            table.append((r[0], mv, int(r[21]), int(r[22]) if r[22] else None))
        start, vol, phf, (k, j) = _rolling_from_rows(table)
        assert _clock24(start) == _clock24(head["PEAK HOUR START"]) == a[f"{p.lower()}_peak_start"]
        assert float(head["PHF"]) == phf == a["phf"][p]
        assert vol == a["volumes"][p]
        summary = rows[next(i for i, r in enumerate(rows) if r[0] == "PEAK-HOUR VOLUMES") + 2]
        assert [int(x) for x in summary[1:13]] == [vol[ap + t] for ap in M.APPR for t in "LTR"]
        leave = M._leaving(vol)
        assert [int(x) for x in summary[17:21]] == [leave[ap] for ap in M.APPR]
        h0 = next(i for i, r in enumerate(rows) if r[0] == "HEAVY-VEHICLE VOLUMES") + 2
        hv = sum(int(r[13]) for r in rows[h0 + k:h0 + k + 12])
        assert round(100 * hv / sum(vol.values()), 1) == a["hv_pct"][p]
        text, ftype, _ = count_agent.read_file(str(SAMPLES / name))
        assert ftype == "csv" and "PEAK HOUR START" in text
        REPORT.append((name, "16 movements, peak start, PHF, HV%, leaving volumes, 13 rolling totals by formula"))


def test_rolling_pdf_text():
    for name in (n for n in FILES if n.startswith("rolling5") and n.endswith(".pdf")):
        a = _ans(name)
        p = "AM" if a["am_peak_start"] else "PM"
        text, warn = count_agent._read_pdf_text(str(SAMPLES / name))
        table = []
        for ln in text.splitlines():
            m = re.match(r"\s*(\d{1,2}:\d\d [AP]M)\s+([\d ]+)$", ln.rstrip())
            if m:
                nums = list(map(int, m.group(2).split()))
                table.append((m.group(1), nums[:16], nums[16], nums[17] if len(nums) > 17 else None))
        assert len(table) == 24 and not warn, len(table)
        start, vol, phf, _ = _rolling_from_rows(table)
        assert _clock24(start) == a[f"{p.lower()}_peak_start"] and vol == a["volumes"][p] and phf == a["phf"][p]
        assert f"PHF {phf:.2f}" in text
        REPORT.append((name, "16 movements, peak start, PHF re-derived from PDF text"))


def test_reference_examples_are_synthetic_and_routable():
    real = count_agent.EXAMPLES_FILES[1]
    count_agent.EXAMPLES_FILES = count_agent.EXAMPLES_FILES[:1]     # committed file only
    count_agent._examples_cache = None
    try:
        ex = count_agent.load_examples()
        by = {v: sum(e["vendor"] == v for e in ex) for v in ("NDS", "Quality Counts")}
        assert len(ex) == 48 and by == {"NDS": 24, "Quality Counts": 24}, by
        for hint in ("NDS", "Quality Counts"):
            assert {e["vendor"] for e in count_agent.select_examples("", vendor_hint=hint)} == {hint}
        banned = re.compile(r"quality ?counts|national data|qualitycounts|counts unlimited|gram traffic|pape|\d{2}-\d{6}-\d{3}", re.I)
        for e in ex:
            assert not banned.search(e["input"]), e["input"][:200]
            out = json.loads(e["output"])
            assert out["vendor_detected"] == M.VENDOR and out["intersections"][0]["volumes"]
    finally:
        count_agent.EXAMPLES_FILES = (count_agent.EXAMPLES_FILES[0], real)
        count_agent._examples_cache = None
    REPORT.append(("reference_examples.jsonl", "48 synthetic examples, 24 per layout, vendor-routable, no vendor text"))


def test_labels_template_matches_columns():
    from openpyxl import load_workbook
    import populate_labels
    wb = load_workbook(populate_labels.TEMPLATE)
    assert wb.sheetnames == ["Labels", "Progress", "ByVendor", "README"]
    assert [c.value for c in wb["Labels"][1]] == populate_labels.COLUMNS and wb["Labels"].max_row == 1
    REPORT.append(("labels_template_blank.xlsx", f"{len(populate_labels.COLUMNS)} columns = populate_labels.COLUMNS"))


def test_local_raw_examples_still_load():
    """Local-only: the owner's real examples (THIRD_PARTY_DIR) are added on top."""
    raw = count_agent.EXAMPLES_FILES[1]
    if not raw.exists():
        print(f"  SKIP test_local_raw_examples_still_load: {raw} not present (raw files are local-only)")
        return
    count_agent._examples_cache = None
    n_raw = sum(1 for _ in open(raw, encoding="utf-8"))
    assert len(count_agent.load_examples()) == 48 + n_raw
    count_agent._examples_cache = None


TESTS = [test_expected_is_reproducible, test_tabular_xlsx_verifier_and_formulas, test_tabular_pdf_text,
         test_rolling_csv_formulas, test_rolling_pdf_text, test_reference_examples_are_synthetic_and_routable,
         test_labels_template_matches_columns, test_local_raw_examples_still_load]

if __name__ == "__main__":       # run_tests.sh runs each file as a script
    for t in TESTS:
        t()
    for f, what in REPORT:
        print(f"  {f}: parsed OK — {what}")
    print(f"test_samples: {len(TESTS)} passed")
