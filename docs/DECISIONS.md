# DECISIONS — settled answers and the one open question

All questions from the grill rounds, with the answer the user gave on 2026-11-16.
Anything not listed here was never asked and must not be assumed.

## Round 1 — scope

| # | Question | Answer |
|---|----------|--------|
| Q1 | PLC platform | **OpenPLC Editor v4 + Runtime v4** (v3 is EOL). Installer supports Arch via pacman; Docker image available. Fallback: port to S7-1200 LAD if required. |
| Q2 | Sort criterion | **Size** (tall vs short). Colour is one isolated block away if it ever changes. |
| Q3 | Mechanism | **3 lanes, 2 pushers, 2 stations in series.** Straight = SMALL, pusher 1 = LARGE, pusher 2 = REJECT. |
| Q4 | Demo | **Simulation only.** No physical kit. Safety hardware claims are design-only and must be stated as such. |
| Q5 | Deliverable depth | **Full engineering package**: I/O map, wiring, state diagram, commented ladder, 34 test cases, report, video, HMI. |
| Q6 | Recovery | **Two step**: Reset clears the latch, Start runs. Never one button. |

## Round 2 — build details

| # | Question | Answer |
|---|----------|--------|
| Q7 | Simulation stack | **OpenPLC v4 + own Python plant simulator via Modbus TCP.** Factory I/O is Windows-only and paid → not a dependency; optional Proton visual later. |
| Q8 | Lane map / fail-safe | **Straight SMALL (de-energized), P1 LARGE, P2 REJECT.** Pneumatic failure never traps a box. |
| Q9 | Unclassified box | **Routed to REJECT lane**, line keeps running, counted and indicated on the amber lamp. Not a fault. |
| Q10 | Safety evidence | **Schematic + optional cheap relay bench video.** Report states "hardware-verified: no" if no kit is used. Software-only E-Stop is rejected. |
| Q11 | HMI | **FUXA web HMI over Modbus TCP** (state, counters, fault cause, Start/Stop). Never safety reset. |
| Q12 | Throughput | **~45 boxes/min as a labelled simulation assumption** (400 mm pitch, 0.3 m/s belt). Not a measurement. |
| Q13 | Size threshold | **LARGE = height ≥ 100 mm; sensor at 60 mm.** Simulator-only number; logic stays boolean. |
| — | Language | **Plain, easy English** for all docs and the report. |

| Q14 | Submission package | **PDF report + source archive (.zip with the OpenPLC project, `sim/`, `fuxa/`) + video link.** If the portal accepts only one file, the report carries the code appendix and a QR/link to the video. |

## Shared understanding — confirmed 2026-11-16

Restated and accepted:

- **Outcome:** 3-lane PLC sorting system with a hardwired E-Stop, proven by simulation and 34 test cases
- **User:** line operator, maintenance tech, DecodeLabs evaluator
- **Why now:** Project 4 is the last step for the "Industrial Automation" skill claim
- **Success:** 10 boxes, 0 mis-sorts; E-Stop stops the motor from all 9 states; complete docs + test log
- **Constraint:** no hardware, so safety claims are design-level, not physically verified
- **Out of scope:** mechanics, pneumatics, vision, SCADA, PL/SIL certification, Factory I/O as a dependency

Frontier is empty. No open questions remain.

## Explicitly rejected ideas

- Software E-Stop only — violates SR-03, the graded requirement
- Guessing a box class on sensor timeout — violates FR-08
- Factory I/O as a required dependency — Windows-only, paid, unofficial on Linux
- One-button reset+start — creates auto-restart behaviour
- Claiming a measured throughput or safety certification

## Facts checked during the grill (not opinions)

| Fact | Source |
|------|--------|
| OpenPLC Runtime v3 is end-of-life | `thiagoralves/OpenPLC_v3` README |
| OpenPLC Runtime v4 supports apt/dnf/yum/pacman/zypper/apk | `Autonomy-Logic/openplc-runtime` README |
| OpenPLC Editor v4 ships Linux x64 and ARM64 builds | `Autonomy-Logic/openplc-editor` README |
| Factory I/O is Windows-only and paid (278€/yr or 765€ one-time, 30-day trial) | factoryio.com |
| Factory I/O talks Modbus TCP, so it could connect to OpenPLC | factoryio.com editions page |
| This machine has no PLC software and no serial PLC adapter | local check |