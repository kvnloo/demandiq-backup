# DemandIQ handoff

Start here every session. Rewrite "Now" in place; don't append session logs.
Goals and step order: [ROADMAP.md](ROADMAP.md). Repo rules: [../CLAUDE.md](../CLAUDE.md).

## Now (2026-11-10)

**Where it stands**
- `main` = everything. No open PRs or issues. Every branch was merged and deleted on 2026-11-10.
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

**Next steps, in order**
1. Run the Forger's live check once API credits are confirmed.
2. Deploy (ROADMAP step 6) once the owner says go.

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
