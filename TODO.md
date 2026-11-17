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

## P1 — Environment setup — DONE

- [x] Install OpenPLC Editor v4 and Runtime v4
  - Runtime v4 runs from `docker-compose.yml` (container `openplc-runtime`, ports
    8443 TLS + 502 Modbus). `docker run` alone is not enough: the plugin config
    and the Modbus slave config are bind-mounted so they survive recreation
  - Editor 4.3.5 AppImage at `~/Applications/OpenPLC-Editor-4.3.5.AppImage`, used
    headlessly: `APPDIR="$PWD" ./AppRun --cli ...` (no display on this machine)
- [x] Connect Editor to runtime: `--cli upload <project> --host 127.0.0.1 --target "OpenPLC Runtime v4"`, credentials via `OPENPLC_USER` / `OPENPLC_PASSWORD`
- [x] Install FUXA (`frangoteam/fuxa`) and confirm access: `http://localhost:1881` answers 200
- [x] Install `pymodbus` for the plant simulator (in `.venv`)
- [x] Smoke test: write a coil and read the words back over Modbus

## P2 — Plant simulator — DONE (`sim/`, see `sim/README.md`)

- [x] `sim/plant.py` — Modbus TCP client: reads the actuator coils, writes the sensor bits
  - Note: on this target no producer exists for `%IX`, so the digital inputs are written
    as `%MX` memory bits (coils 64–77). Tags and logic unchanged; on real hardware with an
    input card they land on `%IX`
- [x] Model: belt motion, box queue with pitch 400 mm, belt 0.3 m/s
- [x] Model both stations, both cylinders (extend/return delays), both home/end sensors
- [x] Scenario files: `acceptance-10.json` (4 small, 3 large, 3 untrustworthy) plus five
  fault scenarios (E-Stop, overload, P1 jam, P2 jam, dead station 1 eye)
- [x] Log each box — id, truth, expectation, lane, entry and done time — to `sim/logs/<name>.log`
- [x] Fault injection flags: `estop_at_s`, `overload_at_s`, `s1_eye_dead_at_s`,
  `cylinder_jam` with `cannot_extend` / `cannot_return`
- [x] `sim/run-scenarios.sh` runs every scenario from a clean PLC state and prints one line each
- [x] `acceptance-10` passes: every box in the expected lane, counters 4 / 3 / 3, no fault (3 runs)
  - the two bugs it exposed were fixed: two separate coil writes were not atomic, so the PLC could
    latch a size reading that did not belong to the box; and an IEC timer only updates `Q` when the
    function block is executed, so the classification timer had to be called every scan


## P3 — Program skeleton (M2) — DONE

- [x] Create OpenPLC project, declare all tags with descriptions — 27 located tags, each with a
  comment in `main.st`; TC-01 proves the program and `docs/ARCHITECTURE.md` agree 1:1
- [x] G1 safety & enable: `E_STOP_OK`, `MOTOR_OL`, run seal, `reset_required` latch
- [x] G2 state machine: written as an IEC 61131-3 **Structured Text `CASE`**, not one-hot rungs
  (the brief allows "ladder or state machine"). States 0-10, state 3 reserved: classification runs
  on every scan outside the `CASE`, and the class memory is an arrival-order FIFO plus `p1_queued`
  rather than one variable, so a busy actuator cannot make a class arrive late
- [x] G3 outputs: one coil per tag, written only in the output block
- [x] G4 timers: T_settle (200 ms classify), T_extend ×2 (800 ms), T_return ×2 (1000 ms),
  T_reject_lamp (1000 ms)
  - **`T_debounce` deliberately dropped**: the scan is 20 ms, so chatter shorter than a scan is
    never seen, and the 200 ms two-sample classification is already a longer filter. A gap in the
    beam *inside* that 200 ms window would still read as two boxes — a documented limitation, and
    the fix at the bench is hardware debounce, not more PLC code (`docs/ARCHITECTURE.md` §8)
- [x] G5 counters: `CNT_SMALL` / `CNT_LARGE` / `CNT_REJECT` in `%QW1..%QW3`, `FAULT_CODE` (0-8) in `%QW4`
- [x] Compile clean and upload (editor CLI answers `"ok": true`, no error warnings)
- [x] Cold start → IDLE
- [x] Run TC-02…TC-14: **14/14 pass** (`docs/TEST-LOG.md`, `sim/tests.py`), and all six scenarios
  pass (`sim/run-scenarios.sh`)

## P4 — Safety (M3) — DONE

- [x] `ESTOP` reachable from every state — TC-16 (E-Stop pressed in states 0, 2, 4, 5, 6, 7, 9 → ESTOP)
- [x] Both valves forced off in ESTOP and FAULT — output guard block; TC-16, TC-26
- [x] `reset_required`: Reset clears latch, Start is separate — TC-18/19/20 (release alone does not restart)
- [x] Pusher extend/return timeouts → retract + FAULT — TC-22 (fault 2), TC-23 (fault 5)
- [x] Pusher-without-box guard (SR-06) — a pusher strokes only while its box is in the zone; a stale record raises fault 8 instead of stroking into air — TC-21
- [x] Motor overload → FAULT (6) — TC-24 (Reset blocked until the cause is cleared)
- [x] FUXA cannot clear `reset_required` (SR-08) — there is no reset coil; TC-25
- [x] Draw `docs/img/wiring.png` (power, contactor, E-Stop, valve supply) — `wiring.svg` → `wiring.png`
- [x] Run TC-15...TC-26, capture evidence — **25 PASS, 1 BLOCKED, 0 FAIL** (`docs/TEST-LOG.md`)
- [ ] Optional: 24 V relay + button bench test for TC-17 video — **not done, no kit**; TC-17 is reported BLOCKED with the design evidence (wiring diagram + SAFETY.md §2/§7), not passed

## P5 — HMI (M4)

- [x] FUXA project: layout, stack light, state, 3 counters, fault cause
- [x] Modbus tag map: coils 0–5 read, coils 80–81 written, holding registers 0–4
- [x] Start/Stop from HMI (never Reset)
- [x] Run TC-27…TC-30 (4 PASS, `docs/TEST-LOG.md`)
- [x] Screenshot for the report (`docs/img/fuxa.png`)

## P6 — Verification (M4)

- [ ] Run TC-01…TC-34, fill `docs/TEST-LOG.md` with results + evidence
- [x] Scripted 10-box run, zero mis-sorts (`sim/scenarios/acceptance-10.json`)
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