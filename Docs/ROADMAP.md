# DemandIQ roadmap

Read this before proposing new work. The owner's words are quoted; everything else is the PM's summary.

## Problem

Engineers get turning-movement counts from many vendors (PDFs, Excel, scans). Today they retype them,
balance the corridor by hand, and grow them to a future year with spreadsheets. It is slow and errors
slip through.

## End goal

On the AI Transportation Tools site the tool is called **Demand Analysis**: "Counts and forecasts.
Reads count sheets from any vendor, balances the volumes and forecasts future traffic."
(Owner, naming: "Demand IQ can be Demand Analysis". The repo keeps the DemandIQ name.)

It feeds the Traffic Analysis tool (TrafficOpsIQ): "if DemandIQ is a precursor to it, volumes will be
transferred". DemandIQ only does volumes and forecasts. LOS, delay and queues belong in TrafficOpsIQ.

## What the app must do

1. Project setup, by typing or by talking.
2. Map the corridor's intersections and configure the approaches.
3. Read count sheets from any vendor (AI reads them, an engineer confirms), or let the engineer type them in.
4. Balance the corridor (code, NCHRP method) and flag anything doubtful.
5. Forecast to the horizon year from the regional model (SANDAG, MTC, HPMS statewide, or the user's own model file).
6. Export the deliverable (Excel, line diagrams) and hand the volumes to Traffic Analysis.

## Rules that hold everywhere

- Code computes every number. AI only reads and suggests, and an engineer accepts or rejects each suggestion.
- "Learning" = the confirmed-example library (`backend/training_data.jsonl`) plus the correction log
  (`backend/capture.py`), never a self-training model. A suite-level agent on the website can use them later
  (trafficiq `docs/AGENT.md`); DemandIQ gets no agent of its own.
- Guideline checks warn. They never block.

## Steps, in order

| # | Step | State |
|---|------|-------|
| 1 | Existing counts S1–S8 (read, balance, QC, export) | Built; pre-launch QC fixes merged (#23) |
| 2 | Forecasting F1–F4 (SANDAG, MTC, HPMS, user model upload) | Built; 200 forecasting tests pass |
| 3 | Known-answer test harness (Examiner for balancing, Forger for PDF reading) | Examiner built + nightly fuzz green; Forger's live check waits on API credits |
| 4 | Fix the through-movement inflation that RECON-ADJUST only discloses | Done (#28, Docs/through-inflation-fix.md) |
| 5 | Project setup by voice (open models) | Merged (#24); works locally, not hosted |
| 6 | Deploy as a free beta at demand.aitransportationtools.com with sign-in | Waiting on the owner's go. Launch order: "Street IQ, then Crash IQ, followed by Demand IQ, and then Traffic Ops IQ" |
| 7 | Hand volumes to Traffic Analysis (TrafficOpsIQ) | Not started |
| 8 | Save engineer corrections from voice and extraction into the example library | Not started |

## Reading count files (decided 2026-11-10)

Owner: "I like your option of having one and two. If two is not able to decipher or if that is wrong,
the cloud helps out." And: "The cloud has a free agent or paid agent. The paid agent will charge them double
the price of the cost." Pricing details come later with the financial model.

Order for every file:
1. **Free reader (code).** Read the text already inside computer-made PDFs, Excel and CSV, with one template
   per common vendor, and use Docling (MIT licence) for computer-made layouts with no template. No AI, no cost,
   the same answer every time.
2. **Checks in code decide if the free read is trustworthy.** The 15-minute rows must add up to the hour,
   approach totals must match their movements, the peak hour must be the busiest four quarters, and the PHF must
   be 0.25–1.0. A scan with no text, or any failed check, sends the file to step 3.
3. **Cloud reader.** The free tier uses an open model; the paid tier uses Claude and is charged to the user at
   2× its cost. The engineer confirms every number either way, as today.

Before anything switches, each reader is scored on the same Forger known-answer sheets (text and scanned
versions) and on the demo PDFs, and the scores come to the owner.

## Guided helper (decided 2026-11-11)

The owner picked A, B, C and D. No AI, no cost: code reads the project state.

- **A. The app works out the next action.** After every click it looks at the project state and picks the single
  next thing to do. It is not a fixed scripted tour, so skipping ahead, going back or reloading still points at the
  right control.
- **B. One ring and one line.** The website's green ring pulses around that one control, with a one-line
  instruction that uses real names, e.g. "Click Union City Blvd at Jean Dr".
- **C. Map helpers.** When the next target is off screen, an arrow on the screen edge points to it. A dotted line
  pulses between two pins on the same street until they are linked, then turns solid with a "linked" label.
- **D. Repeated work moves on by itself.** After the last approach of one street it goes straight to the next
  street, with progress dots like "3 of 5".

Order:
1. Basic UI fixes (readable text, nothing cut off, phones) — PR `fix/ui-basics`, audit
   `Docs/research/2026-11-11-ui-audit.html`.
2. UX re-evaluation: the owner approves the screen-by-screen proposals in that audit (results screens first).
3. A **video mockup** of the guide running on every page, made on the approved new screens, goes to the owner
   before any guide code is written.
4. Build A + B + C + D.

## Later (owner ideas, not scheduled)

- Cheaper reading: a fixed example set per vendor so the API can cache it; Haiku for clean Excel/CSV files,
  only after it scores as well as Sonnet on the known-answer set.
- Camera counts from traffic-vision as another input.
