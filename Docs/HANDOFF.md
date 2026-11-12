# DemandIQ handoff

Start here every session. Rewrite "Now" in place; don't append session logs.
Goals and step order: [ROADMAP.md](ROADMAP.md). Repo rules: [../CLAUDE.md](../CLAUDE.md).

## Now (2026-11-11)

**Where it stands**
- Open PRs: #29 (kvnloo preview bundle) and #30 (hosting on the home PC, another session). Merged 2026-11-11:
  #33 basic UI fixes + audit, #32 free count-file reader (owner: diagram turns flagged for the engineer, startup
  warning when the API key is missing, Docling dropped; `Docs/free-reader.md`).
- CI on `main` is green: backend suite, Examiner harness (schema drift + detectors), frontend lint and unit tests.
  The nightly deep fuzz run passes.
- Not live anywhere. The website card has `url: null`, and nothing is deployed to Render.

**By area**
- **Existing counts (S1–S8).** Reading: Claude Sonnet 4.5 (`backend/count_agent.py`), given 8 of the
  204 confirmed examples in `backend/training_data.jsonl`. Balancing: code (`backend/balancer.py`).
  Every bug the 4-checker pre-launch QC found is fixed (#23). Tolerance is in vehicles: old % values under 0.1
  are read as 0.5 veh.
- **Accuracy gap (fixed, PR #28).** The corridor through at a T-intersection used to take every
  link change (seed 34093 PM int 7 WBT: count 50 → 210). Fixed on `fix/through-inflation`: side-street legs
  with no link now float, so it balances to 85. Over 2,000 harness corridors, those past GEH 5 went 146 → 3.
  Owner kept the floating side street and the "larger" link rule (`Docs/through-inflation-fix.md`).
- **Forecasting (F1–F4).** SANDAG (base year + 2035, 2050), Bay Area MTC, HPMS statewide, and the user's own model file.
  The unpacked model data (`data/sandag`, `data/hpms`, `data/mtc`, ~93 MB) is gitignored, so a clean clone or
  the Docker image can't forecast until it's added. The SANDAG 2022/2035/2050 zips (~20 MB each) are committed
  in `model-drop/`.
- **Harness.** The Examiner (known answers for balancing, tiers A–D) works. The Forger (known answers for PDF reading)
  is built, but its live check needs API credits:
  `python3.12 -m harness.forger.batch --n 20 --style D`.
- **Voice setup (#24).** Whisper + Qwen 2.5 3B on Ollama filled all 8 fields in ~6 s on the Windows PC.
  Its packages (`requirements-voice.txt`) are not in the Docker image yet, so the hosted app would only accept
  typed text.
- **Jev name matching.** Merged. Suggest-only, and the app behaves the same without a key.

- **UI (audit 2026-11-11).** Every screen walked in both themes at desktop and phone with a demo corridor
  (El Cajon Blvd, SANDAG 2025 → 2035). Basic fixes merged (#33): unreadable dropdown lists, pale
  signal colours on the day theme, faint numbers on blue circles, phone screens cut off or squeezed. The bigger UX
  changes are proposals only, in `Docs/research/2026-11-11-ui-audit.html` (results screens first).

**Next steps, in order**
1. Build the approved UX redesign (owner 2026-11-11: all 5 top proposals in `Docs/research/2026-11-11-ui-audit.html`):
   R1 results as one corridor map, R2 one Balancing report page with "Why?" / "Keep counted", R4/R5 future results
   map with background vs project growth, U10 one wording rule (no movement codes, cross-street names, veh/h, AM and
   PM separate), U5/U1 two-question balancing settings + one-line step list on phones. Screenshots to the owner per screen.
2. Video mockup of the guided helper on every page, on the new screens. Owner approves it before any guide code.
3. Build the guided helper: A + B + C + D (ROADMAP "Guided helper").
4. Run the Forger's live Claude check once an API key is on the PC.
5. Deploy (ROADMAP step 6) once the owner says go.

**Blocked on the owner**
- "Go" for deploying DemandIQ. It's a Python server, so it needs a home machine behind Cloudflare Tunnel or a
  cheap VPS. Cloudflare Pages alone can't run it.
- An Anthropic API key on the Windows PC (the Hermes `.env` line is empty) for the Forger's live check.

## Working notes

- Hermes works in `%LOCALAPPDATA%/hermes/worktrees/demandiq-voice`. `repos/demandiq` is the owner's copy.
- `demandiq-hpms`, `-jev` and `-qc` in the same folder are old Mac file copies with no git. Everything in them
  is on `main` except ignored run outputs.
- The cron job "Traffic repos auto-push" pushes unpushed commits every 30 min.
- Lessons go in `harness/knowledge/lessons.md`.
