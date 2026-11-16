# TODO — Project 4, PLC-Based Conveyor Sorting System

Rule: do not start a task before its dependency is checked. One deliverable per milestone.

## P0 — Decisions — DONE

- [x] Q1 platform: OpenPLC Editor + Runtime v4 (native/Docker on Arch)
- [x] Q2 sort criterion: size
- [x] Q3 3 lanes, 2 pushers, 2 stations in series
- [x] Q4 simulation only
- [x] Q5 full engineering package
- [x] Q6 two-step Reset then Start
- [x] Q7 OpenPLC v4 + own Modbus plant simulator
- [x] Q8 lane map: straight SMALL, P1 LARGE, P2 REJECT
- [x] Q9 unclassified → REJECT lane, line keeps running
- [x] Q10 safety evidence: schematic + optional relay demo, stated as design-only
- [x] Q11 FUXA web HMI
- [x] Q12 throughput as labelled assumption (~45 boxes/min)
- [x] Q13 size threshold 100 mm, sensor at 60 mm
- [x] Language: plain English for all docs and report
- [x] Q14 submission: PDF report + source zip + video link.

## P1 — Environment setup

- [ ] Install OpenPLC Editor v4 (Linux x64 release) and Runtime v4
  - Docker path: `docker run -d --name openplc-runtime -p 8443:8443 ghcr.io/autonomy-logic/openplc-runtime:latest`
  - Native path: `sudo ./install.sh --native` (installer handles pacman)
- [ ] Connect Editor to runtime (port 8443, TLS self-signed)
- [ ] Install FUXA (Docker: `frangoteam/fuxa`) and confirm browser access
- [ ] Install `pymodbus` for the plant simulator: `pip install pymodbus`
- [ ] Smoke test: write one coil from the simulator, read one input

## P2 — Plant simulator (do this before the ladder, it is the test harness)

- [ ] `sim/plant.py` — Modbus TCP client: reads coils (outputs), writes discrete inputs (sensors)
- [ ] Model: belt motion, box queue with pitch 400 mm, belt 0.3 m/s
- [ ] Model both stations, both cylinders (extend/return delays), both home/end sensors
- [ ] Scenario file for the 10-box acceptance run (4 small, 3 large, 3 forced reject)
- [ ] Log each box: id, class, lane, timestamp → `sim/run.log`
- [ ] Fault injection flags: sensor timeout, cylinder jam, overload

## P3 — Program skeleton (M2)

- [ ] Create OpenPLC project, declare all tags with descriptions
- [ ] G1 safety & enable: `E_STOP_OK`, `MOTOR_OL`, run seal, `reset_required` latch
- [ ] G2 state machine: 11 states, one-hot rungs, `box_class` memory
- [ ] G3 outputs: one coil per rung, no duplicate tags
- [ ] G4 timers: T_classify, T_extend×2, T_return×2, T_reject_lamp, T_debounce
- [ ] G5 counters: count_SMALL / LARGE / REJECT, fault_cause word
- [ ] Compile clean, cold start → IDLE
- [ ] Run TC-02…TC-14

## P4 — Safety (M3)

- [ ] `ESTOP` reachable from every state
- [ ] Both valves forced off in ESTOP and FAULT
- [ ] `reset_required`: Reset clears latch, Start is separate
- [ ] Pusher extend/return timeouts → retract + FAULT
- [ ] Pusher-without-box guard (SR-06)
- [ ] Motor overload → FAULT
- [ ] FUXA cannot clear `reset_required` (SR-08)
- [ ] Draw `docs/img/wiring.png` (power, contactor, E-Stop, valve supply)
- [ ] Run TC-15…TC-26, capture evidence
- [ ] Optional: 24 V relay + button bench test for TC-17 video

## P5 — HMI (M4)

- [ ] FUXA project: layout, stack light, state, 3 counters, fault cause
- [ ] Modbus tag map: coils 0–10, holding registers 0–4
- [ ] Start/Stop from HMI (never Reset)
- [ ] Run TC-27…TC-30
- [ ] Screenshot for the report

## P6 — Verification (M4)

- [ ] Run TC-01…TC-34, fill `docs/TEST-LOG.md` with results + evidence
- [ ] Scripted 10-box run, zero mis-sorts
- [ ] Record video: 10-box cycle, E-Stop mid-divert, two-step recovery, HMI

## P7 — Documentation and submission (M5)

- [ ] Commented ladder printout (PDF, all rungs, headers)
- [ ] Report: cover, brief restatement, architecture, I/O table, state diagram, safety section with honest limitations, test log, conclusion
- [ ] Update README status and layout
- [ ] Commit `src/`, `sim/`, `fuxa/`, `docs/`
- [ ] Confirm portal format, submit

## Stretch (only if ahead)

- [ ] Second size threshold (3 size classes, uses spare I/O)
- [ ] Port to Siemens S7-1200 LAD to prove NFR-03 on a second platform
- [ ] Counter reset from HMI with confirmation dialog
- [ ] Trend/recorder page in FUXA for throughput
- [ ] Factory I/O via Proton purely as a 3D visual for the video (not a dependency)

## Never do

- Software-only E-Stop (SR-03)
- Guess a class on sensor timeout (FR-08)
- Claim a measured throughput number without belt data (NFR-05)
- Claim safety certification (SAFETY.md §8)
- Let FUXA clear a safety latch (SR-08)