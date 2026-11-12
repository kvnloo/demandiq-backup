# Third-party files: removed from the repo (2026-11-12)

Owner decision (2026-11-11): no third-party or client material in the repo.
Raw vendor count reports, labels built from them, and agency model output left
the tree. The owner keeps the raw files on his own disk. The product and the
tests now run on **synthetic look-alikes** of every format (owner add,
2026-11-12: "make sample files with sample numbers, keep the formula and the
format, and create test cases").

**History is not rewritten yet.** The files are still in old commits. The PM
runs the rewrite after this PR merges (see [History rewrite](#history-rewrite-pending)).

## What was removed

| Path (tree) | Size | What it was | Who read it | Replaced by |
|---|---|---|---|---|
| `backend/training_data.jsonl` | 4.2 MB | 204 vendor count reports (Quality Counts CSV/PDF, NDS XLSX/PDF) pasted in full as few-shot examples: project ids, job numbers, client locations | `count_agent.load_examples` (8 per call, vendor-matched) | `backend/reference_examples.jsonl`: 48 synthetic examples (24 per layout), same layouts and answer schema |
| `backend/tools/labels_template.xlsx` | 233 KB | Labeling workbook: 627 rows extracted from real count files (file names, intersections, volumes) | `tools/populate_labels.py` (template copy), `tools/export_for_finetuning.py` (default input) | `backend/tools/labels_template_blank.xlsx`: same 4 sheets, columns and formulas, no rows |
| `backend/tools/labels_template_OG.xlsx` | 261 KB | Older copy of the same | nothing | nothing |
| `model-drop/2022.zip`, `2035.zip`, `2050.zip` | 63 MB | SANDAG regional model output (`hwyLoad` shapefiles) | nothing in code (docs only) | `backend/tests/fixtures/samples/network/model_drop_2035.zip` (same zip layout, synthetic network) |
| `backend/tests/forecasting/fixtures/sandag_{2025,2050}/hwyLoad_*.{shp,shx,dbf,prj,cpg}` | 200 KB | 147-link clips of SANDAG model output | `tests/forecasting/test_upload_network.py` | `backend/tests/fixtures/samples/network/sandag_{2025,2050}/` (synthetic, same schema) |
| `backend/tests/forecasting/fixtures/renamed_{2025,2050}.zip` | 45 KB | Same clips with renamed columns | same test | `samples/network/renamed_{2025,2050}.zip` |
| `backend/tests/forecasting/fixtures/make_fixtures.py` | 3 KB | Our script that cut the clips from the real files | nobody | `samples/make_samples.py` |

Only in history (not in today's tree): root-level `training_data.jsonl`,
`labels_template.xlsx`, `labels_template_OG.xlsx` (first commit, moved later)
and root `2035.zip` (uploaded, then moved to `model-drop/`).

Audited and **kept** (not third-party): UI screenshots and mockups in
`Docs/demandiq_tour/` and `Docs/research/` (our own app, example numbers), the
constructed Alpine corridor in `Docs/demandiq_tour/v1_corridor*`,
`backend/tests/through_inflation_cases.json` (harness seeds),
`harness/goldens/`, `scripts/fixtures/forecast.json`. The three
`backend/tests/fixtures/TMC_*.pdf` on branch `feat/free-reader` are
ReportLab-made demo sheets marked "SAMPLE DATA - not a field count"; they are
backed up but are not on the rewrite list.

## Where the raw files live

`THIRD_PARTY_DIR`, default `~/Workspace/projects/demandiq/third-party-private`
(set the env var to move it). Paths inside it mirror the repo, every version
from history is there, `MANIFEST.json` lists each file's git blob id (all
checked byte-for-byte), and `demandiq-before-cleanup-2026-11-12.bundle` is a
full `git bundle --all` of the repo before this change.

Code that still uses the raw files, locally only:

- `count_agent` adds `THIRD_PARTY_DIR/backend/training_data.jsonl` to the
  synthetic examples when it exists (owner's machine: 48 + 204 examples).
- `tools/populate_labels.py` and `tools/export_for_finetuning.py` now default
  to `THIRD_PARTY_DIR/backend/tools/labels_template.xlsx` and
  `THIRD_PARTY_DIR/backend/training_data.jsonl`, so new real labels never land
  in the repo. `.gitignore` also blocks those names anywhere.
- Two local-only tests: `test_samples.py::test_local_raw_examples_still_load`
  and `test_upload_network.py::test_real_sandag_clip_still_uploads`. Without
  the folder they skip and say why.

## The synthetic samples

`backend/tests/fixtures/samples/` (see its README). One seeded generator,
`make_samples.py`, writes the 15-minute multi-sheet report (XLSX + PDF), the
5-minute rolling report (CSV + PDF), the SANDAG-schema network, the few-shot
examples and the blank labels workbook. The counts are invented; everything
else (hourly totals, peak hour, PHF, heavy-vehicle %, leaving volumes, link
totals and lengths) is computed by the formula the real files use.
`expected.json` holds the answers.

CI runs, on every push:
- `backend/test_samples.py` (in `run_tests.sh`): `count_verifier` and
  `count_agent`'s file readers on every sample, exact against `expected.json`;
  the arithmetic re-derived from each file; `expected.json` re-derived from the
  generator; the few-shot file checked for vendor text and per-layout routing.
- `tests/forecasting/test_upload_network.py` on the synthetic network, in a
  new CI step that installs the geo stack (before, CI skipped this file).

Not in CI because it costs API tokens:
`python tests/fixtures/samples/check_extraction.py` runs the real Claude
extraction on all 8 sample files and prints fields matched per file. Caveat:
the few-shot examples come from the same generator (other seeds), so this
measures reading these layouts, not real-world accuracy.

## Effect on extraction

Production (and CI) used 204 real examples; now it uses 48 synthetic ones.
Vendor routing is unchanged: a hint of NDS or Quality Counts still gets 8
same-layout examples (checked offline). The example block is ~22 K chars vs
~26 K before. **Real-world extraction accuracy before/after was not measured**:
there is no API key in this environment, and the repo's live check (Forger /
`check_extraction.py`) needs one. Run `check_extraction.py` and the Forger
batch with a key before relying on the synthetic set; if real accuracy drops,
the fallback is to ship derived numbers, not raw text.

## History rewrite (pending)

The PM runs this once, after the PR merges, on a fresh mirror clone. It removes
every version of these paths from all commits; the new synthetic files use
different names, so they survive.

`paths-to-remove.txt`:

```
training_data.jsonl
labels_template.xlsx
labels_template_OG.xlsx
2035.zip
backend/training_data.jsonl
backend/tools/labels_template.xlsx
backend/tools/labels_template_OG.xlsx
model-drop/2022.zip
model-drop/2035.zip
model-drop/2050.zip
backend/tests/forecasting/fixtures/renamed_2025.zip
backend/tests/forecasting/fixtures/renamed_2050.zip
backend/tests/forecasting/fixtures/sandag_2025/
backend/tests/forecasting/fixtures/sandag_2050/
```

```bash
git clone --mirror https://github.com/redhawkliger/demandiq.git && cd demandiq.git
git filter-repo --invert-paths --paths-from-file ../paths-to-remove.txt
# check: both must print nothing
git log --all --format=%h -- $(cat ../paths-to-remove.txt)
git rev-list --all --objects | grep -E 'training_data\.jsonl|labels_template(_OG)?\.xlsx|model-drop/.*\.zip|^[0-9a-f]+ 2035\.zip|hwyLoad_20(25|50)|renamed_20(25|50)\.zip'
git push --force --mirror
```

After the force-push: open PRs and other clones must re-clone or rebase;
GitHub keeps old objects reachable from closed PR refs and caches until
GitHub support purges them, so ask support to run a GC if the files must be
unreachable on github.com too.
