# Free count-file reader

Every count file now goes through three steps (owner decision, ROADMAP "Reading count files"):

1. **Free code reader** (`backend/free_reader.py`). Reads the text inside computer-made PDFs (pdfplumber),
   Excel and CSV. No AI, no cost.
2. **Code checks** (`backend/read_checks.py`) decide whether that reading can be trusted.
3. Anything the free reader can't read, any scan with no text layer, and any reading that fails a check goes to
   the **cloud reader** (`backend/count_agent.py`, unchanged).

The engineer confirms every number either way. Each `/extract` result now says which reader was used and why:
`reader: "free" | "cloud"` and `reader_reason` (for example `tabular; all checks passed`, or
`free reader: no text layer (a scan)`).

## What it reads

- **Tables whose columns are named by movement** (`NL NT NR NU SL ...` or `NBL NBT ...`): the Forger's style T,
  the three Union City demo sheets, NDS / Quality Counts Excel workbooks, CSV. Columns are mapped to movements
  by their header names only. If a row has a different number of cells than its header, the file goes to the cloud.
- **The Forger's style D diagram** (Counts Unlimited-like), by the fixed positions of each number on the drawing.

Everything else goes to the cloud: scans, files with more than one intersection, multi-page PDFs that repeat the
same 15-min rows (e.g. Total + Cars + Trucks pages), and files missing an AM or PM count.

## The checks

Run on each period. Any failure sends the file to the cloud.

- Each 15-min row adds up to its printed row total.
- The four peak 15-min rows add up to the printed peak-hour row.
- Approach totals match their movements, and the grand total matches all movements.
- The peak hour is the busiest 4 consecutive quarters inside AM 06:00-10:00 / PM 14:00-19:00.
- PHF is between 0.25 and 1.0.
- Every value is a whole number, 0 or more.
- At least one printed total has to tie out. A sheet with nothing to check against goes to the cloud.

## Scores (no API calls)

`python -m harness.forger.free_batch --n 50`, seeds 1-50:

| Sheets | Read exactly right | Wrong but passed the checks | Handed to the cloud |
|---|---|---|---|
| T (table), 50 | 50 | 0 | 0 |
| D (diagram), 50 | 50 | 0 | 0 |
| T scanned, 50 | 0 | 0 | 50 (no text layer) |
| D scanned, 50 | 0 | 0 | 50 (no text layer) |

"Exactly right" means all 16 movements in both periods, both peak-hour start times and PHF match the answer key.
(D sheets print no 15-min data, so their right PHF is "none".) No sheet was partly right.

In plain words: 50 of 50 table sheets and 50 of 50 diagram sheets were read exactly, and 0 wrong numbers were let
through. All 100 scans were handed to the cloud.

**Misreads made on purpose** (one AM peak-hour value changed after reading, before the checks), 50 sheets each:

| Misread | T caught | D caught |
|---|---|---|
| One value off by 10 (EBT) | 50 | 50 |
| Left and right swapped in one approach (EBL/EBR) | 50 | **0** |
| Through swapped between approaches (NBT/SBT) | 50 | 50 |

On D the left/right swap gets through because the diagram prints no 15-min rows, only totals, and a swap inside
one approach doesn't change any total. Only the fixed position template protects D from that. See OPEN 1.

**Real and demo files:**

- 3 Union City demo PDFs (`backend/tests/fixtures/`): all 3 read by the free reader, every check passed. The values
  match the sheets' printed Peak Hr rows. There is no separate answer key in the repo, so this is checked against
  the sheet itself, not against independent known values.
- `Test/24-080045-001 Oakwood Dr & El Camino Real.xlsx` (the owner's NDS workbook, gitignored): read by the free
  reader. All 32 movements, both peak hours (07:45, 15:45), PHF (0.956, 0.970) and HV% (2.6, 1.9) match the
  confirmed reading in `backend/training_data.jsonl`. `test_free_reader.py` checks this when the file is on the
  machine and skips it in CI.

## Docling (dropped)

On these sheets Docling found **no tables** (it read the demo table as a paragraph and a ruled grid as an image),
and it costs ~550 MB (torch). The owner dropped it on 2026-11-11.

## Owner decisions (2026-11-11: "all the 3 use recommended")

1. **DECIDED: read diagrams for free, and ask the engineer to confirm left/right turns.** Each diagram read adds a
   warning listing every approach's left and right turn to check against the sheet. Was: **Trust D diagrams on totals alone?** A left/right swap inside one approach passes every check (0 of 50 caught).
   The template reads them right (50 of 50), but no check confirms it. Options: keep it, or send every diagram
   without 15-min rows to the cloud.
2. **DECIDED: keep starting without the key, with a loud warning.** The server logs "cloud reader is OFF" at
   startup and `/health` reports `cloud_reader`. Was: **Missing key no longer stops the app.** `app.py` used to refuse to start without `ANTHROPIC_API_KEY`. Now it
   starts, the free reader works, and only the files it hands off get the existing "ANTHROPIC_API_KEY is not set"
   error. The downside: a deploy with a forgotten key isn't caught at startup.
3. **DECIDED: Docling dropped.** Was: **Keep Docling?** It found nothing on our sheets and costs ~550 MB. Keep it optional, add it to the image, or
   remove it. A real vendor PDF where pdfplumber fails would settle this. The repo has none.
4. **Real vendor templates.** The diagram template only matches the Forger's drawing. Real Counts Unlimited, GRAM
   and Pape-Dawson PDFs go to the cloud until there are sample files to build templates from.
5. **U-turns.** A table with U-turn columns gives 0. A table or diagram without them gives null. This is the cloud
   reader's rule, carried over.

## Run it

```bash
python -m harness.forger.free_batch --n 50              # scores above, ~20 s, no API
cd backend && python3.12 test_read_checks.py && python3.12 test_free_reader.py
DQ_TEST_XLSX="<path to Oakwood .xlsx>" python3.12 test_free_reader.py   # include the real workbook
```
