# Synthetic samples of third-party formats

Made by `make_samples.py` (seed 2026). Re-run after changing it:

    cd backend && python tests/fixtures/samples/make_samples.py

Nothing here is real: vendor "Sample Counts Co.", streets "Sample St &
Example Ave", ids SAMPLE-###, city "Sampleton". Counts are invented; every
other number is computed by the formula the real format uses. Answers are in
`expected.json` (count_agent's output schema).

| File | Mimics | Layout kept | What it tests |
|---|---|---|---|
| `tabular15_{1,2}.xlsx` | 15-minute turning-movement workbook (NDS-style) | 6 sheets (Total, Cars, HT, Total Trucks, Total Bikes, Bikes); header block; AM / NOON / PM sections of 96 15-min rows; NL..WU columns + TOTAL; TOTAL VOLUMES, APPROACH %'s, PEAK HR, PEAK HR VOL, PEAK HR FACTOR (per movement, intersection, approach) | `count_verifier` re-sums all 32 peak-hour movements and both PHFs; HV% = (Total − Cars) / Total; `count_agent.read_file` |
| `tabular15_{1,2}.pdf` | Same report as PDF | one page per sheet with the in-window rows and the peak rows, plus a peak-hour summary page | peak volumes, PHF, peak start read from pdfplumber text |
| `rolling5_{1,2}.csv` | 5-minute report with rolling hourly totals (Quality Counts-style), one count period | header (peak hour, peak 15 min, PHF), peak-hour volumes with entering/leaving, % heavy vehicles, peds, micromobility, peak 15-min flowrates, all-vehicle 5-min table with Hourly Totals, heavy-vehicle / bus / ped / bike / scooter tables | rolling totals, peak hour, PHF = V / (4 × peak 15 min), HV%, leaving = the three movements into each leg |
| `rolling5_{1,2}.pdf` | Same report as PDF | header, volume and HV% diagrams as text, 5-min table, flowrates | peak hour, volumes, PHF re-derived from the PDF text |
| `network/sandag_{2025,2050}/hwyLoad_*` | SANDAG loaded-highway shapefile (EPSG:2230) | the 20 columns the adapter reads; `total_flow` = sum of the 10 period flows; `len_mile` = length / 5280; zone connectors (fc 10), ramp, locals | upload, column guess, ingest, match and forecast equivalence (`test_upload_network.py`) |
| `network/renamed_{2025,2050}.zip` | the same with an unknown MPO's column names, in a folder inside a zip | | column-guess heuristics |
| `network/model_drop_2035.zip` | `model-drop/<year>.zip` layout (`2035/hwyLoad.*`) | | upload reads it as SANDAG |

Also written by the generator: `backend/reference_examples.jsonl` (count_agent's
48 few-shot examples, other seeds than these files) and
`backend/tools/labels_template_blank.xlsx`.

Not reproduced: the lane-configuration grid of the 5-minute CSV, the drawn
intersection diagram graphics and the vendor logos (the PDFs are text only).

Checks: `backend/test_samples.py` (offline, CI), `tests/forecasting/test_upload_network.py`
(geo stack, CI), `check_extraction.py` (live Claude extraction, needs ANTHROPIC_API_KEY).
