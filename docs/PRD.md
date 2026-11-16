# PRD — PLC-Based Conveyor Sorting System

DecodeLabs Project 4 (optional mastery phase). Role: Robotics & Automation Engineer.

## 1. Problem

Boxes arrive on a conveyor and must be separated by size into three lanes: small (accept), large (accept, different lane), and reject (unclassified). A human standing at the line would be slow, inconsistent, and close to moving machinery. The line must sort by itself, with operator Start/Stop and a safety stop that always wins.

## 2. Users

| Who | Need |
|-----|------|
| Line operator | Start/Stop/Reset, see state at a glance |
| Maintenance tech | I/O map, tag forcing, fault cause |
| DecodeLabs evaluator | Evidence of I/O mapping, sequence logic, real safety interlocking |
| Me | A defensible "Industrial Automation" skill claim |

## 3. Success metrics

| Metric | Target | How measured |
|--------|--------|--------------|
| Sorting accuracy | 100% on test set (NFR-04) | Scripted simulator run, 10 boxes |
| Throughput | ~45 boxes/min, labelled simulation assumption | 400 mm pitch, 0.3 m/s belt |
| E-Stop coverage | Works from all 9 states | TC-15, TC-16 |
| Illegal valve states | 0 | TC-21, TC-32 |
| Documentation | I/O map, state diagram, commented ladder, HMI shots | Review |

## 4. Scope

### In
- Size classification (tall vs short), 3 lanes, 2 pushers in series
- Full I/O map + IEC 61131-3 ladder on OpenPLC v4
- Safety: hardwired E-Stop in the contactor coil circuit, reset-required latch, pusher timeouts, motor overload, fault cause word
- FUXA web HMI (state, counters, fault, Start/Stop), Modbus TCP
- Scripted plant simulator for repeatable tests (NFR-07)
- Test plan, test log, report, video

### Out
- Mechanical and pneumatic design, guards fabrication
- Vision / colour classification
- SCADA/MES, cloud, networking beyond local Modbus TCP
- Safety certification (PL/SIL)
- Factory I/O as a dependency

## 5. Decisions

| # | Decision | Why |
|---|----------|-----|
| D1 | Sort by **size** | Boolean sensor, no calibration, matches "digital inputs" in the brief |
| D2 | **3 lanes, 2 pushers, 2 stations in series** | Pushers cannot share one zone; straight lane is the de-energized fail-safe default |
| D3 | Lane map: straight = SMALL, P1 = LARGE, P2 = REJECT | Valve failure only mis-routes a large box, never traps one |
| D4 | Safety is **hardwired**, not software | A hung PLC cannot release a software interlock |
| D5 | **OpenPLC Editor + Runtime v4** on Arch (native or Docker) | Free, Linux-supported, runs on this machine |
| D6 | **FUXA** web HMI over Modbus TCP | Linux-native, free, recorder + tags; optional for the brief but requested |
| D7 | Simulation-only demo, scripted simulator | No hardware available; repeatable evidence |
| D8 | Unclassified → REJECT lane, line keeps running | The reject lane exists for this; stopping per odd box looks fragile |
| D9 | Reset and Start are separate buttons | Two-step recovery is provable evidence against auto-restart |
| D10 | Size threshold 100 mm, sensor at 60 mm | Round numbers, simulator-only, logic stays boolean |

## 6. Assumptions

- A1: PLC-class runtime needs ≤ 16 DI / 8 DO
- A2: Sensors are 24 VDC PNP 3-wire (simulated as Modbus discrete inputs)
- A3: Motor is switched by a contactor with a 24 VDC coil
- A4: Pushers are 5/2 single-solenoid valves, spring return
- A5: Only one box between station 1 and station 2 at a time
- A6: E-Stop hardware verification is design-only (no kit)

## 7. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| No PLC hardware | Safety cannot be hardware-verified | State it plainly in the report; provide schematic + optional cheap relay demo |
| FUXA + OpenPLC Modbus mapping fiddly | HMI shows wrong values | Verify with `mbpoll`/`pymodbus` before trusting the screen (TC-27) |
| Two pushers = more states | Sequence bugs | Explicit state table, one-hot rungs, scripted sim tests |
| Factory I/O temptation | Windows-only, paid, Proton risk | Not a dependency; OpenPLC sim is the source of truth |
| Evaluator expects S7 | Source format mismatch | Plain ladder, only portable instructions (TC-34) |
| Throughput number taken as measured | Credibility loss | Label as assumption in PRD, README and report |

## 8. Milestones

| M | Deliverable | Gate |
|---|-------------|------|
| M1 | Requirements, PRD, I/O map | Tags + safe states complete |
| M2 | State machine + ladder v1 | All 11 states walkable in simulator |
| M3 | Safety interlocks | TC-15…TC-26 pass |
| M4 | FUXA HMI + scripted tests | TC-27…TC-31 pass |
| M5 | Report, printouts, video | Portal-ready |

## 9. Remaining open question

Only one: **submission format accepted by the DecodeLabs portal** (PDF report + source + video link assumed). Confirm before M5.
All other questions are settled in `docs/DECISIONS.md`.