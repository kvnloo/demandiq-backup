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
| 4 | Fix the through-movement inflation that RECON-ADJUST only discloses | Not started |
| 5 | Project setup by voice (open models) | Merged (#24); works locally, not hosted |
| 6 | Deploy as a free beta at demand.aitransportationtools.com with sign-in | Waiting on the owner's go. Launch order: "Street IQ, then Crash IQ, followed by Demand IQ, and then Traffic Ops IQ" |
| 7 | Hand volumes to Traffic Analysis (TrafficOpsIQ) | Not started |
| 8 | Save engineer corrections from voice and extraction into the example library | Not started |

## Later (owner ideas, not scheduled)

- Cheaper reading: a fixed example set per vendor so the API can cache it; Haiku for clean Excel/CSV files,
  only after it scores as well as Sonnet on the known-answer set.
- Camera counts from traffic-vision as another input.
