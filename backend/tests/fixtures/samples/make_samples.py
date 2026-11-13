"""Synthetic look-alikes of every third-party file format DemandIQ reads.

    cd backend && python tests/fixtures/samples/make_samples.py

One seeded generator. It invents the 5/15-minute counts, then derives every
other number by the formula the real reports use (bins -> hourly totals ->
peak hour -> PHF -> heavy-vehicle %), writes the files in the real layouts and
writes the answers to expected.json. No real company, place, project id or
logo appears anywhere: the vendor is "Sample Counts Co.", streets are
"Sample St & Example Ave", ids are SAMPLE-###.

Outputs (this folder unless noted):
  tabular15_*.xlsx / .pdf   15-min, 16-movement, multi-sheet report (NDS-style)
  rolling5_*.csv / .pdf     5-min report with rolling hourly totals (QC-style)
  network/                  SANDAG hwyLoad-schema model network (shapefile + zips)
  expected.json             answer keys, straight from the generator's numbers
  ../../../reference_examples.jsonl          few-shot examples count_agent reads
  ../../../tools/labels_template_blank.xlsx  empty labeling workbook

Counts need only the stdlib (so CI can re-derive expected.json); writing files
needs openpyxl, reportlab + pdfplumber (PDFs, examples) and geopandas (network).
"""
import csv
import datetime as dt
import json
import random
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
SEED = 2026
VENDOR = "Sample Counts Co."
DATE = dt.date(2026, 1, 13)
APPR = ("NB", "SB", "EB", "WB")
TURNS = ("L", "T", "R", "U")
MOVES = [a + t for a in ("EB", "WB", "NB", "SB") for t in ("U", "L", "T", "R")]   # count_agent order
NS_STREETS = ["Sample St", "Demo Rd", "Test Ave", "Mock Pkwy"]
EW_STREETS = ["Example Ave", "Placeholder Blvd", "Fixture Way", "Specimen Dr"]
N_TABULAR, N_ROLLING = 2, 2          # committed sample files per layout
N_EXAMPLES = 12                      # few-shot examples per layout file type


# ── counts (pure, stdlib) ────────────────────────────────────────────────────
def _rates(rng, uturns):
    r = {}
    ew_main = rng.random() < 0.5
    for a in APPR:
        main = (a in ("EB", "WB")) == ew_main
        r[a + "T"] = rng.randint(500, 1400) if main else rng.randint(40, 400)
        r[a + "L"] = rng.randint(20, 220)
        r[a + "R"] = rng.randint(15, 180)
        r[a + "U"] = rng.randint(0, 12) if uturns else 0
    return r


def _bins(rng, rates, n, per_hour):
    """Integer counts per movement per bin; a peak bump somewhere in the window."""
    mid = rng.uniform(0.3, 0.7) * n
    shape = [1 + 0.35 * max(0.0, 1 - abs(i - mid) / (n / 3)) for i in range(n)]
    return {mv: [max(0, round(r / per_hour * shape[i] * rng.uniform(0.7, 1.3))) for i in range(n)]
            for mv, r in rates.items()}


def _split(rng, bins, lo, hi):
    """A share of each bin (heavy vehicles / bikes); never more than the bin."""
    return {mv: [min(v, round(v * rng.uniform(lo, hi))) for v in b] for mv, b in bins.items()}


def _peak(totals, width):
    """First window of `width` bins with the largest sum -> start index."""
    sums = [sum(totals[i:i + width]) for i in range(len(totals) - width + 1)]
    return sums.index(max(sums))


def _hhmm(t):
    return f"{t.hour:02d}:{t.minute:02d}"


def _times(start, step, n):
    t0 = dt.datetime.combine(DATE, start)
    return [(t0 + dt.timedelta(minutes=step * i)).time() for i in range(n)]


def _period_answer(bins, hv, step):
    """Peak hour, volumes, PHF, HV% for one period — the formulas the reports use."""
    per_hour, per_15 = 60 // step, 15 // step
    n = len(next(iter(bins.values())))
    tot = [sum(b[i] for b in bins.values()) for i in range(n)]
    k = _peak(tot, per_hour)
    vol = {mv: sum(bins[mv][k:k + per_hour]) for mv in MOVES}
    v = sum(vol.values())
    j = k + _peak(tot[k:k + per_hour], per_15)          # peak 15 minutes inside the peak hour
    v15 = sum(tot[j:j + per_15])
    hv_v = sum(sum(hv[mv][k:k + per_hour]) for mv in MOVES)
    return {"k": k, "j": j, "vol": vol, "v15": v15,
            "phf_exact": v / (4 * v15), "hv_pct": round(100 * hv_v / v, 1)}


def tabular_sample(i, seed=SEED):
    """15-minute report: AM 07:00-08:45 and PM 16:00-17:45, 8 bins each."""
    rng = random.Random(f"{seed}-tabular-{i}")
    s = {"layout": "tabular15", "id": f"SAMPLE-{100 + i:03d}",
         "ns": rng.choice(NS_STREETS), "ew": rng.choice(EW_STREETS), "periods": {}}
    s["name"] = f"{s['ns']} & {s['ew']}"
    for p, start in (("AM", dt.time(7, 0)), ("PM", dt.time(16, 0))):
        bins = _bins(rng, _rates(rng, uturns=i % 2 == 1), 8, 4)
        hv = _split(rng, bins, 0.0, 0.06)
        bikes = _split(rng, bins, 0.0, 0.01)
        a = _period_answer(bins, hv, 15)
        s["periods"][p] = {"times": _times(start, 15, 8), "total": bins, "hv": hv, "bikes": bikes, **a}
    return s


def rolling_sample(i, seed=SEED):
    """5-minute report for one count period (24 bins), like the source format."""
    rng = random.Random(f"{seed}-rolling-{i}")
    p = "AM" if i % 2 == 0 else "PM"
    start = dt.time(7, 0) if p == "AM" else dt.time(16, 0)
    s = {"layout": "rolling5", "id": f"SAMPLE-{200 + i:03d}",
         "ns": rng.choice(NS_STREETS), "ew": rng.choice(EW_STREETS), "periods": {}}
    s["name"] = f"{s['ns']} & {s['ew']}"
    bins = _bins(rng, _rates(rng, uturns=False), 24, 12)
    hv = _split(rng, bins, 0.0, 0.05)
    bikes = _split(rng, bins, 0.0, 0.01)
    peds = {leg: [rng.randint(0, 3) for _ in range(24)] for leg in ("South", "North", "West", "East")}
    a = _period_answer(bins, hv, 5)
    s["periods"][p] = {"times": _times(start, 5, 24), "total": bins, "hv": hv, "bikes": bikes, "peds": peds, **a}
    return s


def answer(s):
    """Expected extraction, in count_agent's output schema."""
    out = {"name": s["name"], "am_peak_start": None, "pm_peak_start": None,
           "volumes": {"AM": {mv: None for mv in MOVES}, "PM": {mv: None for mv in MOVES}},
           "phf": {"AM": None, "PM": None}, "hv_pct": {"AM": None, "PM": None}}
    for p, d in s["periods"].items():
        out[f"{p.lower()}_peak_start"] = _hhmm(d["times"][d["k"]])
        out["volumes"][p] = dict(d["vol"])
        # tabular reports print PHF to 3 places, the rolling report to 2
        out["phf"][p] = round(d["phf_exact"], 3 if s["layout"] == "tabular15" else 2)
        out["hv_pct"][p] = d["hv_pct"]
    return {"vendor_detected": VENDOR, "intersections": [out]}


def expected():
    out = {}
    for i in range(1, N_TABULAR + 1):
        s = tabular_sample(i)
        for ext in ("xlsx", "pdf"):
            out[f"tabular15_{i}.{ext}"] = answer(s)
    for i in range(1, N_ROLLING + 1):
        s = rolling_sample(i)
        for ext in ("csv", "pdf"):
            out[f"rolling5_{i}.{ext}"] = answer(s)
    return out


# ── tabular15: 6-sheet workbook + PDF ────────────────────────────────────────
T_COLS = ["NL", "NT", "NR", "NU", "SL", "ST", "SR", "SU", "EL", "ET", "ER", "EU", "WL", "WT", "WR", "WU"]
T_MV = [c[0] + "B" + c[1] for c in T_COLS]
LANES = [0, 1, 0, 0, 0, 1, 0, 0, 1, 2, 0, 0, 1, 2, 0, 0]
SHEETS = ("Data - Total", "Data - Cars", "Data - HT", "Data - Total Trucks", "Data - Total Bikes", "Data - Bikes")


def _sheet_bins(d, sheet):
    if sheet == "Data - Total":
        return d["total"]
    if sheet == "Data - Cars":
        return {mv: [t - h for t, h in zip(d["total"][mv], d["hv"][mv])] for mv in MOVES}
    if sheet in ("Data - HT", "Data - Total Trucks"):
        return d["hv"]
    return d["bikes"]


def _summary(bins, k):
    """TOTAL VOLUMES / APPROACH % / PEAK HR VOL / PEAK HR FACTOR rows, by formula."""
    tot = [sum(bins[mv]) for mv in T_MV]
    appr = [sum(tot[a * 4:a * 4 + 4]) for a in range(4)]
    pct = [t / appr[c // 4] if appr[c // 4] else 0 for c, t in enumerate(tot)]
    vol = [sum(bins[mv][k:k + 4]) for mv in T_MV]
    mph = [v / (4 * max(bins[mv][k:k + 4])) if v else 0 for v, mv in zip(vol, T_MV)]
    iv = [sum(bins[mv][i] for mv in T_MV) for i in range(k, k + 4)]
    aph = []
    for a in range(4):
        av = [sum(bins[mv][i] for mv in T_MV[a * 4:a * 4 + 4]) for i in range(k, k + 4)]
        aph.append(sum(av) / (4 * max(av)) if sum(av) else 0)
    return tot, pct, vol, mph, (sum(vol) / (4 * max(iv)) if sum(vol) else 0), aph


def _peak_label(times, k):
    end = (dt.datetime.combine(DATE, times[k]) + dt.timedelta(hours=1)).time()
    return f"{times[k]:%I:%M %p} - {end:%I:%M %p}"


def write_tabular_xlsx(s, path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for sheet in SHEETS:
        ws = wb.create_sheet(sheet)
        ws.append(["Location:", s["name"]])
        ws.append(["City:", "Sampleton"] + [None] * 12 + ["Project ID:", s["id"]])
        ws.append(["Control:", "Signalized"] + [None] * 12 + ["Date:", dt.datetime.combine(DATE, dt.time())])
        ws.append([None, sheet])
        ws.append(["NS/EW Streets:", s["ns"], None, None, None, s["ns"], None, None, None,
                   s["ew"], None, None, None, s["ew"]])
        for sec in ("AM", "NOON", "PM"):
            ws.append([sec, "  NORTHBOUND", None, None, None, "  SOUTHBOUND", None, None, None,
                       "  EASTBOUND", None, None, None, "  WESTBOUND"])
            ws.append([None] + LANES)
            ws.append([None] + T_COLS + ["TOTAL"])
            d = s["periods"].get(sec)
            rows = {}
            if d:
                b = _sheet_bins(d, sheet)
                rows = {t: [b[mv][i] for mv in T_MV] for i, t in enumerate(d["times"])}
            for q in range(96):
                t = dt.time(q // 4, q % 4 * 15)
                vals = rows.get(t)
                ws.append([t] + (vals + [sum(vals)] if vals else [None] * 16 + [0]))
            ws.append([None] + T_COLS + ["TOTAL"])
            if not d:
                ws.append(["APPROACH %'s :"])
                ws.append(["PEAK HR :"])
                continue
            tot, pct, vol, mph, phf, aph = _summary(b, d["k"])
            ws.append(["TOTAL VOLUMES :"] + tot + [sum(tot)])
            ws.append(["APPROACH %'s :"] + pct)
            ws.append(["PEAK HR :", _peak_label(d["times"], d["k"]), None, None, None,
                       d["times"][d["k"]]] + [None] * 11 + ["TOTAL"])
            ws.append(["PEAK HR VOL :"] + vol + [sum(vol)])
            ws.append(["PEAK HR FACTOR :"] + mph + [phf])
            ws.append([None, aph[0], None, None, None, aph[1], None, None, None, aph[2], None, None, None, aph[3]])
            if sec == "AM":
                ws.append(["Headers"] + T_MV)
    wb.properties.creator = VENDOR
    wb.properties.created = wb.properties.modified = dt.datetime(2026, 1, 13)
    wb.save(path)


def _pdf(path, pages, title):
    """Monospaced text pages — what pdfplumber hands count_agent."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setTitle(title)
    c.setAuthor(VENDOR)
    c.setSubject("synthetic sample - not a field count")
    for lines in pages:
        y = 760
        for ln in lines:
            c.setFont("Courier", 7)
            c.drawString(28, y, ln)
            y -= 9
        c.showPage()
    c.save()


def _row(label, vals, w=5, lw=18):
    return f"{label:>{lw}} " + "".join(f"{v:<{w}}" for v in vals)


def write_tabular_pdf(s, path):
    pages = []
    for n, sheet in enumerate(SHEETS[:5], 1):     # the PDF prints Bikes once
        L = [f"{VENDOR:^100}", f"{'Intersection Turning Movement Count':^100}", "",
             f"   Location: {s['name']}",
             f"   City: Sampleton{'':40}Project ID: {s['id']}",
             f"   Control: Signalized{'':36}Date: {DATE:%m/%d/%Y}",
             f"{sheet:^100}",
             f"   NS/EW Streets: {s['ns']}  {s['ns']}  {s['ew']}  {s['ew']}",
             f"{'':19}NORTHBOUND          SOUTHBOUND          EASTBOUND           WESTBOUND"]
        for p in ("AM", "PM"):
            d = s["periods"][p]
            b = _sheet_bins(d, sheet)
            tot, pct, vol, mph, phf, _ = _summary(b, d["k"])
            L += [_row(p, LANES), _row("", T_COLS + ["TOTAL"])]
            for i, t in enumerate(d["times"]):
                vals = [b[mv][i] for mv in T_MV]
                L.append(_row(f"{t:%I:%M %p}".lstrip("0"), vals + [sum(vals)]))
            L += [_row("TOTAL VOLUMES :", tot + [sum(tot)]),
                  _row("APPROACH %'s :", [f"{x:.0%}" for x in pct], 5),
                  f"{'PEAK HR :':>18} {_peak_label(d['times'], d['k'])}{'':40}TOTAL",
                  _row("PEAK HR VOL :", vol + [sum(vol)]),
                  _row("PEAK HR FACTOR :", [f"{x:.3f}" for x in mph + [phf]], 6), ""]
        L.append(f"{'':80}Page {n} of 6")
        pages.append(L)
    L = [f"{'Peak Hour Turning Movement Count':^100}", "", f"   {s['name']}   {s['id']}", ""]
    for p in ("AM", "PM"):
        d = s["periods"][p]
        L.append(f"   {p} PEAK HOUR {_peak_label(d['times'], d['k'])}   PHF {d['phf_exact']:.3f}"
                 f"   HV {d['hv_pct']}%")
        for a in APPR:
            L.append(f"      {a}:  L {d['vol'][a + 'L']:>5}  T {d['vol'][a + 'T']:>5}  R {d['vol'][a + 'R']:>5}"
                     f"  U {d['vol'][a + 'U']:>4}   approach {sum(d['vol'][a + t] for t in TURNS):>6}")
        L.append("")
    pages.append(L + [f"{'':80}Page 6 of 6"])
    _pdf(path, pages, f"Turning Movement Count - {s['name']}")


# ── rolling5: CSV + PDF ──────────────────────────────────────────────────────
R_COLS = [(a, t) for a in APPR for t in ("Left", "Thru", "Right", "U-Turn", "RTOR")]
LTR = [(a, t) for a in APPR for t in ("Left", "Thru", "Right")]
_T = {"Left": "L", "Thru": "T", "Right": "R", "U-Turn": "U"}
LEG = {"NB": "South", "SB": "North", "EB": "West", "WB": "East"}
LEGS = ("South", "North", "West", "East")


def _leaving(vol):
    """Vehicles leaving on each leg = the three movements that turn into it."""
    return {"NB": vol["NBT"] + vol["EBL"] + vol["WBR"], "SB": vol["SBT"] + vol["WBL"] + vol["EBR"],
            "EB": vol["EBT"] + vol["SBL"] + vol["NBR"], "WB": vol["WBT"] + vol["NBL"] + vol["SBR"]}


def _clock(t):
    return f"{t:%I:%M %p}".lstrip("0")


def _pct(h, v):
    return round(100 * h / v, 1) if v else 0


def _rolling(s):
    """Everything the 5-minute report prints, derived from the bins."""
    p, d = next(iter(s["periods"].items()))
    k, j, times = d["k"], d["j"], d["times"]
    vol = d["vol"]
    hvv = {mv: sum(d["hv"][mv][k:k + 12]) for mv in MOVES}
    after = lambda i, m: _clock((dt.datetime.combine(DATE, times[i]) + dt.timedelta(minutes=m)).time())  # noqa: E731
    return {"d": d, "k": k, "j": j, "times": times, "vol": vol, "hvv": hvv,
            "ent": {a: sum(vol[a + t] for t in "LTR") for a in APPR},
            "hent": {a: sum(hvv[a + t] for t in "LTR") for a in APPR},
            "leave": _leaving(vol), "hleave": _leaving(hvv),
            "f15": lambda b, mv: 4 * sum(b[mv][j:j + 3]),
            "f15all": lambda b: 4 * sum(sum(v[j:j + 3]) for v in b.values()),
            "tot": [sum(d["total"][mv][i] for mv in MOVES) for i in range(24)],
            "peak": (_clock(times[k]), after(k, 60)), "peak15": (_clock(times[j]), after(j, 15))}


def write_rolling_csv(s, path):
    r = _rolling(s)
    d, k, vol, hvv, ent, leave, f15 = r["d"], r["k"], r["vol"], r["hvv"], r["ent"], r["leave"], r["f15"]
    rows = []
    add = lambda *c: rows.append(list(c) + [""] * (23 - len(c)))  # noqa: E731
    add(f"{VENDOR.upper()} REPORT"); add("=" * 21); add()
    add("Intersection:", f"1. {s['ns']} ", s["ew"])
    add("City/State:", "Sampleton", "ZZ"); add("JobNo:", s["id"]); add("ClientID:", "")
    add("Date:", f"{DATE:%m/%d/%y}"); add("Comments:", ""); add("Latitude/Longitude", "", "")
    add("PEAK HOUR START", r["peak"][0]); add("PEAK HOUR END", r["peak"][1])
    add("PEAK 15-MIN START", r["peak15"][0]); add("PEAK 15-MIN END", r["peak15"][1])
    add("PHF", f"{round(d['phf_exact'], 2):.2f}"); add()
    hdr = [f"{a}{t}" for a, t in LTR] + [f"{a}Entering" for a in APPR] + [f"{a}Leaving" for a in APPR]
    add("PEAK-HOUR VOLUMES"); add("", *hdr)
    add("", *[vol[a + _T[t]] for a, t in LTR], *[ent[a] for a in APPR], *[leave[a] for a in APPR]); add()
    add("PERCENT HEAVY VEHICLES"); add("", *hdr)
    add("HEAVY VEHICLES", *[_pct(hvv[a + _T[t]], vol[a + _T[t]]) for a, t in LTR],
        *[_pct(r["hent"][a], ent[a]) for a in APPR], *[_pct(r["hleave"][a], leave[a]) for a in APPR])
    add("BUSES"); add()
    add("PEAK-HOUR VOLUMES - PEDESTRIANS"); add("Leg/Crosswalk", *LEGS)
    add("", *[sum(d["peds"][leg][k:k + 12]) for leg in LEGS]); add()
    add("PEAK-HOUR VOLUMES - MICROMOBILITY"); add("", *[f"{a}{t}" for a, t in LTR])
    add("Bicycles", *[sum(d["bikes"][a + _T[t]][k:k + 12]) for a, t in LTR]); add("Scooters"); add()
    add("PEAK 15-MIN FLOWRATES")
    add("VehicleType", *[f"{a}{t}" for a, t in R_COLS], "Total")
    add("All Vehicles", *[f15(d["total"], a + _T[t]) if t != "RTOR" else 0 for a, t in R_COLS],
        r["f15all"](d["total"]))
    add("Heavy Trucks", *[f15(d["hv"], a + _T[t]) if t in ("Left", "Thru", "Right") else "" for a, t in R_COLS],
        r["f15all"](d["hv"]))
    add("Buses")
    add("Pedestrians", *[4 * sum(d["peds"][LEG[a]][r["j"]:r["j"] + 3]) if t == "Thru" else "" for a, t in R_COLS],
        r["f15all"](d["peds"]))
    add("Bicycles", *[f15(d["bikes"], a + _T[t]) if t in ("Left", "Thru", "Right") else "" for a, t in R_COLS],
        r["f15all"](d["bikes"]))
    add("Scooters"); add(); add()
    add("ALL-VEHICLE VOLUMES")
    add("Time Period", *[f"{a} {t}" for a, t in R_COLS], "Total", "Hourly Totals")
    tot = r["tot"]
    for i, t in enumerate(r["times"]):
        add(_clock(t), *[d["total"][a + _T[t2]][i] if t2 != "RTOR" else "" for a, t2 in R_COLS],
            tot[i], sum(tot[i - 11:i + 1]) if i >= 11 else "")
    add(); add()
    for title, b in (("HEAVY-VEHICLE VOLUMES", d["hv"]), ("BUS VOLUMES", None)):
        add(title); add("Time Period", *[f"{a} {t}" for a, t in LTR], "Total")
        for i, t in enumerate(r["times"] if b else []):
            vals = [b[a + _T[t2]][i] for a, t2 in LTR]
            add(_clock(t), *vals, sum(vals))
        add()
    add("PEDESTRIAN VOLUMES"); add("Time Period", *[f"{leg} Leg" for leg in LEGS], "Total")
    for i, t in enumerate(r["times"]):
        vals = [d["peds"][leg][i] for leg in LEGS]
        add(_clock(t), *vals, sum(vals))
    add()
    add("BICYCLE VOLUMES"); add("Time Period", *[f"{a} {t}" for a, t in LTR], "Total")
    for i, t in enumerate(r["times"]):
        vals = [d["bikes"][a + _T[t2]][i] for a, t2 in LTR]
        add(_clock(t), *vals, sum(vals))
    add()
    add("SCOOTER VOLUMES"); add("Time Period", *[f"{a} {t}" for a, t in LTR], "Total")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f, lineterminator="\r\n").writerows(rows)


def write_rolling_pdf(s, path):
    r = _rolling(s)
    d, vol, hvv, f15 = r["d"], r["vol"], r["hvv"], r["f15"]
    v = lambda mv: vol[mv]  # noqa: E731
    h = lambda mv: _pct(hvv[mv], vol[mv])  # noqa: E731
    L = ["  Type of peak hour being reported: Intersection Peak   Method for determining peak hour: Total Entering Volume",
         f"   LOCATION: 1. {s['ns']} -- {s['ew']}{'':20}JOB #: {s['id']}",
         f"   CITY/STATE: Sampleton, ZZ{'':26}DATE: {DATE:%a, %b} {DATE.day} {DATE.year}",
         f"{'':30}Peak-Hour: {r['peak'][0]} -- {r['peak'][1]}",
         f"{'':30}Peak 15-Min: {r['peak15'][0]} -- {r['peak15'][1]}", "",
         "   VOLUMES                                             PERCENT HEAVY VEHICLES",
         f"{'':12}SB  R {v('SBR'):>5}  T {v('SBT'):>5}  L {v('SBL'):>5}{'':14}SB  {h('SBR')} {h('SBT')} {h('SBL')}",
         f"   EB  L {v('EBL'):>5}{'':26}WB  R {v('WBR'):>5}{'':4}EB  L {h('EBL')}   WB  R {h('WBR')}",
         f"       T {v('EBT'):>5}{'':10}PHF {round(d['phf_exact'], 2):.2f}{'':8}T {v('WBT'):>5}{'':4}    T {h('EBT')}       T {h('WBT')}",
         f"       R {v('EBR'):>5}{'':30}L {v('WBL'):>5}{'':4}    R {h('EBR')}       L {h('WBL')}",
         f"{'':12}NB  L {v('NBL'):>5}  T {v('NBT'):>5}  R {v('NBR'):>5}{'':14}NB  {h('NBL')} {h('NBT')} {h('NBR')}",
         "   ENTERING  " + "  ".join(f"{a} {r['ent'][a]}" for a in APPR)
         + "    LEAVING  " + "  ".join(f"{a} {r['leave'][a]}" for a in APPR), "",
         f"   5-Min Count   {s['ns']} (Northbound)  {s['ns']} (Southbound)  {s['ew']} (Eastbound)  {s['ew']} (Westbound)",
         "   Period        " + "Left Thru Right U    " * 4 + "Total  Hourly",
         "   Beginning At" + f"{'':86}Totals"]
    tot = r["tot"]
    for i, t in enumerate(r["times"]):
        vals = [d["total"][a + x][i] for a in APPR for x in TURNS]
        L.append(f"   {_clock(t):>8}     " + " ".join(f"{x:<4}" for x in vals)
                 + f"  {tot[i]:<6}{sum(tot[i - 11:i + 1]) if i >= 11 else ''}")
    L += ["", "   Peak 15-Min      Northbound           Southbound           Eastbound            Westbound",
          "   Flowrates     " + "Left Thru Right U    " * 4 + "Total",
          "   All Vehicles  " + " ".join(f"{f15(d['total'], a + x):<4}" for a in APPR for x in TURNS)
          + f"  {r['f15all'](d['total'])}",
          "   Heavy Trucks  " + " ".join(f"{f15(d['hv'], a + x):<4}" for a in APPR for x in TURNS)
          + f"  {r['f15all'](d['hv'])}",
          "   Buses", "   Pedestrians   " + "  ".join(f"{4 * sum(d['peds'][LEG[a]][r['j']:r['j'] + 3])}" for a in APPR),
          "   Bicycles      " + " ".join(f"{f15(d['bikes'], a + x):<4}" for a in APPR for x in "LTR"),
          "   Scooters", "   Comments:",
          f"   Report generated on {DATE:%m/%d/%Y} SOURCE: {VENDOR} (synthetic sample - not a field count)",
          f"{'':45}Page 1 of 1"]
    _pdf(path, [L], f"Turning Movement Count - {s['name']}")


# ── few-shot reference examples for count_agent ──────────────────────────────
ROUTING = {"tabular15": "NDS", "rolling5": "Quality Counts"}   # count_agent's vendor-match tag


def reference_examples():
    """N_EXAMPLES per layout file type, each with its exact answer. Seeds differ
    from the committed samples, so the samples stay a held-out check."""
    import tempfile
    sys.path.insert(0, str(BACKEND))
    from count_agent import MAX_EXAMPLE_CHARS, _read_excel, _read_pdf_text, _read_text
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(N_EXAMPLES):
            for layout, make, writers in (
                    ("tabular15", tabular_sample, (("xlsx", write_tabular_xlsx, _read_excel),
                                                   ("pdf", write_tabular_pdf, _read_pdf_text))),
                    ("rolling5", rolling_sample, (("csv", write_rolling_csv, _read_text),
                                                  ("pdf", write_rolling_pdf, _read_pdf_text)))):
                s = make(i, seed=SEED + 1)
                for ext, write, read in writers:
                    f = Path(tmp) / f"{s['id']} {s['name']}.{ext}"
                    write(s, f)
                    text = read(str(f))[0][:MAX_EXAMPLE_CHARS]
                    ans = answer(s)
                    ans["intersections"][0]["notes"] = (f"Synthetic {layout} sample; peak hour, PHF and HV% "
                                                        f"computed from the file's own interval bins.")
                    out.append({"vendor": ROUTING[layout], "messages": [
                        {"role": "user", "content": f"Filename: {f.name}\n\nFile content:\n{text}"},
                        {"role": "assistant", "content": json.dumps(ans)}]})
    return out


# ── labeling workbook (structure only, no rows) ──────────────────────────────
def write_labels_template(path):
    sys.path.insert(0, str(BACKEND / "tools"))
    from openpyxl import Workbook
    from populate_labels import COLUMNS
    wb = Workbook()
    ws = wb.active
    ws.title = "Labels"
    ws.append(COLUMNS)
    ws.freeze_panes = "E2"
    col = {c: ws.cell(row=1, column=i + 1).column_letter for i, c in enumerate(COLUMNS)}
    vs, cf, vd = col["verification_status"], col["confirmed"], col["vendor"]
    pr = wb.create_sheet("Progress")
    for r in (["DemandIQ — Labeling Progress"], [], ["Metric", "Count"],
              ["Total files processed", "=COUNTA(Labels!A2:A1978)"],
              ["Verified (auto-passed)", f'=COUNTIF(Labels!{vs}2:{vs}1978,"VERIFIED")'],
              ["Check (needs review)", f'=COUNTIF(Labels!{vs}2:{vs}1978,"CHECK")'],
              ["Mismatch (must fix)", f'=COUNTIF(Labels!{vs}2:{vs}1978,"MISMATCH")'], [],
              ["Confirmed by user", f"=COUNTIF(Labels!{cf}2:{cf}1978,TRUE)"],
              ["Target for fine-tuning", 500], ["Remaining to target", "=MAX(0,B10-B9)"],
              ["Progress to target", "=IFERROR(B9/B10,0)"]):
        pr.append(r)
    bv = wb.create_sheet("ByVendor")
    for r in (["Confirmed labels by vendor"], [], ["Vendor", "Confirmed", "Total processed"]):
        bv.append(r)
    for v in ("NDS", "Quality Counts", "Counts Unlimited", "GRAM", "Pape-Dawson", "Unknown"):
        bv.append([v, f'=COUNTIFS(Labels!{vd}2:{vd}1978,"{v}",Labels!{cf}2:{cf}1978,TRUE)',
                   f'=COUNTIF(Labels!{vd}2:{vd}1978,"{v}")'])
    rd = wb.create_sheet("README")
    for r in (["DemandIQ — Labeling Workflow"], [],
              ["Purpose", "Labeling worksheet. Each row = one count file you have labeled. Real labels "
                          "live outside the repo (THIRD_PARTY_DIR); this blank copy is the template."], [],
              ["Workflow", "1. Run:  python populate_labels.py --input <folder>\n2. Open Labels: new rows are "
                           "pre-filled.\n3. Check verification_status (VERIFIED / CHECK / MISMATCH).\n4. Correct "
                           "numbers against the raw file.\n5. Set confirmed = TRUE.\n6. python "
                           "export_for_finetuning.py --append-confirmed"], [],
              ["Important rules", "• U-turns are SEPARATE movements. Never merge into Left.\n• Peak times in "
                                  "HH:MM 24-hour (e.g. 07:45, 15:45).\n• HV% = heavy vehicles / total × 100.\n"
                                  "• Leave cell blank if data not applicable."]):
        rd.append(r)
    wb.properties.created = wb.properties.modified = dt.datetime(2026, 1, 13)
    wb.save(path)


# ── model network (SANDAG hwyLoad schema) ────────────────────────────────────
NET_ANCHOR = (-117.05, 32.76)       # an arbitrary point inside EPSG:2230's zone
RENAME = {"hwycov_id": "LINKNO", "link_name": "STREET", "len_mile": "MILES", "fc": "CLASS",
          "fc_desc": "CLASSNAME", "from_node": "ANODE", "to_node": "BNODE", "total_flow": "ADT",
          "ab_am_flow": "AM_AB_VOL", "ba_am_flow": "AM_BA_VOL", "ab_pm_flow": "PM_AB_VOL",
          "ba_pm_flow": "PM_BA_VOL", "ab_am_lane": "LANES_AB", "ba_am_lane": "LANES_BA"}
FC = {3: "Major Arterial", 5: "Local Collector", 7: "Local (non-circulation element) Road",
      9: "Local Ramp", 10: "Zone Connector"}
PERIODS = ("ea", "am", "md", "pm", "ev")


def network_frame(year, seed=SEED):
    """A grid: one arterial (SAMPLE BLVD) crossed by six streets, a parallel
    local road, zone connectors and a ramp. Horizon years grow every flow;
    geometry and ids never change. total_flow = sum of the ten period flows,
    len_mile = geometry length / 5280 (the source file's own relationships)."""
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString
    rng = random.Random(f"{seed}-network")
    grow = random.Random(f"{seed}-network-{year}")
    f = 1.0 if year <= 2025 else 1 + (year - 2025) / 100
    e0, n0 = Transformer.from_crs(4326, 2230, always_xy=True).transform(*NET_ANCHOR)
    pt = lambda c, r: (e0 + 1500 * c, n0 + 1200 * r)  # noqa: E731
    node = lambda c, r: int(10000 + 1000 * (c + 2) + 10 * (r + 2))  # noqa: E731
    links = []

    def link(a, b, name, fc, lanes, base):
        flows = {}
        for p, share in zip(PERIODS, (0.05, 0.2, 0.35, 0.25, 0.15)):
            for d in ("ab", "ba"):
                flows[f"{d}_{p}_flow"] = base * share * rng.uniform(0.7, 1.3) * f * grow.uniform(0.95, 1.1)
        g = LineString([pt(*a), pt(*b)])
        links.append({"hwycov_id": len(links) + 1, "link_name": name, "len_mile": g.length / 5280, "fc": fc,
                      "fc_desc": FC[fc], "from_node": node(*a), "to_node": node(*b),
                      "total_flow": sum(flows.values()), **flows,
                      "ab_am_lane": lanes, "ba_am_lane": lanes, "geometry": g})

    for c in range(-2, 3):
        link((c, 0), (c + 1, 0), "SAMPLE BLVD", 3, 2, 20000)
        link((c, 1), (c + 1, 1), "EXAMPLE LN", 7, 1, 2500)
    for c in range(-2, 4):
        link((c, 0), (c, 1), f"TEST ST {c + 3}", 5, 1, 6000)
        link((c, -1), (c, 0), f"TEST ST {c + 3}", 5, 1, 6000)
        link((c, 1), (c, 2), "TAZ Connector", 10, 1, 1500)   # connectors hang off the outer nodes
        link((c, -2), (c, -1), "TAZ Connector", 10, 1, 1500)
    for c in (0, 1):                                        # and one inside each studied radius
        link((c + 0.3, -0.4), (c + 0.1, -0.1), "TAZ Connector", 10, 1, 800)
    link((3, -1), (4, -1), "SAMPLE BLVD ON RAMP", 9, 1, 5000)
    gdf = gpd.GeoDataFrame(links, geometry="geometry", crs="EPSG:2230")
    to4326 = Transformer.from_crs(2230, 4326, always_xy=True)
    intx = [{"num": n, "lat": round(lat, 9), "lng": round(lng, 9)}
            for n, (lng, lat) in enumerate((to4326.transform(*pt(c, 0)) for c in (0, 1)), 1)]
    return gdf, intx


def _zip_layer(gdf, out_zip, folder, stem=None):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / f"{stem or folder}.shp")
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(Path(tmp).iterdir()):
                z.write(p, f"{folder}/{p.name}")   # a folder inside the zip, like a real export


def write_network(out):
    out.mkdir(exist_ok=True)
    for year in (2025, 2050):
        gdf, intx = network_frame(year)
        d = out / f"sandag_{year}"
        d.mkdir(exist_ok=True)
        gdf.to_file(d / f"hwyLoad_{year}.shp")
        _zip_layer(gdf.rename(columns=RENAME), out / f"renamed_{year}.zip", f"network_{year}")
    gdf, _ = network_frame(2035)
    _zip_layer(gdf, out / "model_drop_2035.zip", "2035", stem="hwyLoad")   # model-drop/<year>.zip layout
    return {"intersections": intx, "feature_count": len(gdf)}


def main():
    exp = expected()
    for i in range(1, N_TABULAR + 1):
        s = tabular_sample(i)
        write_tabular_xlsx(s, HERE / f"tabular15_{i}.xlsx")
        write_tabular_pdf(s, HERE / f"tabular15_{i}.pdf")
    for i in range(1, N_ROLLING + 1):
        s = rolling_sample(i)
        write_rolling_csv(s, HERE / f"rolling5_{i}.csv")
        write_rolling_pdf(s, HERE / f"rolling5_{i}.pdf")
    net = write_network(HERE / "network")
    (HERE / "expected.json").write_text(
        json.dumps({"seed": SEED, "files": exp, "network": net}, indent=1) + "\n", encoding="utf-8", newline="\n")
    with open(BACKEND / "reference_examples.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for ex in reference_examples():
            f.write(json.dumps(ex) + "\n")
    write_labels_template(BACKEND / "tools" / "labels_template_blank.xlsx")
    print("wrote", len(exp), "count samples, network/, reference_examples.jsonl, tools/labels_template_blank.xlsx")


if __name__ == "__main__":
    main()
