# Project 4 — PLC-Based Conveyor Sorting System

DecodeLabs Robotics & Automation, Batch 2026 — optional mastery phase.
Track: **Industrial Control & Safety**.

Goal from the brief: *"Design the industrial control logic to sort boxes by size or color on an automated conveyor line."*

## What the PDF actually asks for

| ID | Requirement (PDF wording) |
|----|---------------------------|
| R1 | Map digital Inputs (sensors) and Outputs (motors, pneumatic pushers) |
| R2 | Write industrial Ladder Logic **or** a State Machine to control the sequence of operations |
| R3 | Implement strict safety interlocks (e.g., E-Stop logic halts all motors immediately) |

Everything else (platform, lane count, numbers) is a decision we made and recorded in `docs/DECISIONS.md` — not something the PDF specifies.

## Build

- **Sort by size** (tall ≥ 100 mm).
- **3 lanes, 2 pushers, 2 stations in series:** straight = SMALL, pusher 1 = LARGE, pusher 2 = REJECT.
- Fail-safe default: valves de-energized = both cylinders retracted = small boxes still sort correctly.
- **E-Stop is hardwired** into the contactor coil circuit, so the PLC cannot defeat it.
- **OpenPLC Editor + Runtime v4**, IEC 61131-3 state machine in Structured Text (the brief allows ladder *or* a state machine). **FUXA** web HMI over Modbus TCP.
- **Simulation only:** a scripted plant simulator drives the PLC over Modbus TCP, so every test is repeatable.

## Deliverables

1. I/O map with tag, address, safe state — `docs/ARCHITECTURE.md`
2. State machine and sequence — `docs/ARCHITECTURE.md`
3. Structured Text state-machine program with comments — `src/`
4. Safety design and honest limits — `docs/SAFETY.md`
5. 34 test cases + executed log — `docs/TEST-PLAN.md`, `docs/TEST-LOG.md`
6. Plant simulator + scenario files — `sim/`
7. FUXA HMI project + screenshots — `fuxa/`
8. Report and demo video for the portal

## Repo layout

```
README.md
TODO.md
docs/REQUIREMENTS.md   traceable requirements
docs/PRD.md            scope, decisions, risks, milestones
docs/ARCHITECTURE.md   layout, I/O map, state machine, program structure, HMI map
docs/SAFETY.md         E-Stop, interlocks, safety matrix, limitations
docs/TEST-PLAN.md      TC-01 … TC-34
docs/DECISIONS.md      every grill answer + the one open question
src/                   OpenPLC program (exported project + Structured Text source)
sim/                   Python plant simulator (Modbus TCP) + scenarios
fuxa/                  HMI project
docs/img/              diagrams and screenshots
```

## Quickstart

```bash
# 1. PLC runtime (Docker path)
docker run -d --name openplc-runtime -p 8443:8443 \
  ghcr.io/autonomy-logic/openplc-runtime:latest

# 2. HMI
docker run -d --name fuxa -p 1881:1881 frangoteam/fuxa

# 3. Simulator dependencies
pip install pymodbus

# 4. Open the program in OpenPLC Editor v4, connect to localhost:8443, upload, start
# 5. Run a scenario
python sim/plant.py --scenario sim/scenarios/acceptance-10.json
```

Native OpenPLC install (Arch supported by the installer):

```bash
git clone https://github.com/Autonomy-Logic/openplc-runtime.git
cd openplc-runtime && sudo ./install.sh --native
```

## Numbers that are assumptions, not measurements

- Belt pitch 400 mm, speed 0.3 m/s → ~45 boxes/min (theoretical)
- Size threshold 100 mm, sensor height 60 mm
- Cylinder timings: extend 800 ms, return 1000 ms

Replace them if you get real belt data.

## Status

- **P0 Documentation** — complete (`docs/`, `TODO.md`).
- **P1 Environment** — OpenPLC Runtime v4 + FUXA run from `docker-compose.yml`. Modbus TCP on port 502, served by the project's own Modbus server so the I/O image sizes come from `project.json`.
- **P2 Plant simulator** — `sim/plant.py` plus six scenarios and `sim/run-scenarios.sh` (`sim/README.md`). Models the belt, the box queue, both stations, both cylinders and their home/extended sensors, and injects faults: E-Stop, overload, a jammed pusher (extend or return) and a dead station 1 eye. Every box is logged with its truth, its expectation, its lane and its timestamps. All six scenarios pass.
- **P3 Program** — Structured Text state machine in `src/conveyor-sorter/conveyor-sorter/pous/programs/main.st`. Compiles, uploads and runs headlessly. Two layers: classification runs on every scan so a busy actuator sequence can never sample air instead of the box, and the state machine only drives the actuators.
- **Acceptance run (TC-13, 10 boxes)** — **green.** `sim/scenarios/acceptance-10.json`: 4 small, 3 large, 3 boxes whose height reading is not trustworthy. Every box lands in the lane it should, counters read 4 / 3 / 3, no faults. All five fault scenarios pass too (`bash sim/run-scenarios.sh`).
- **TC-01 … TC-26** — **25 PASS, 1 BLOCKED, 0 FAIL** (`.venv/bin/python sim/tests.py`, results and evidence in `docs/TEST-LOG.md`): the I/O map matches the program tag for tag, cold start, Start/Stop, Start refused while the E-Stop is pressed, all three lanes, the short-pulse edge rule, both return sequences, the two-box case, and the P4 safety cases.
- **P4 Safety** — E-Stop reachable from every state; both valves forced off in ESTOP and FAULT; two-step recovery (Reset, then a separate Start — releasing the button never restarts the line); pusher extend/return timeouts; the **SR-06** guard (a pusher strokes only while its box is still in the zone — a stale record raises fault 8 instead of stroking into air or letting the box slip down the SMALL lane); motor overload; and no HMI reset path (SR-08). Wiring evidence: `docs/img/wiring.png` (motor power, contactor coil circuit, E-Stop, valve supply, PLC I/O).
- **TC-17 (hardware independence) is BLOCKED, not passed** — proving the E-Stop cuts the motor with the PLC ignored needs the physical relay kit, which is not available. The design evidence is the wiring diagram and `docs/SAFETY.md` §2/§7, and the report states it as blocked.
- **One deviation from the test plan, stated openly:** `T_debounce` is not implemented; the 20 ms scan and the 200 ms two-sample classification are the filter, and a beam gap inside that window would still read as two boxes (`docs/ARCHITECTURE.md` §8).
- No hardware has ever been connected. Every safety claim in `docs/SAFETY.md` is design-level only.

## Notes

- The PDF is a 17-slide Canva deck; only the "Project 4" slide carries technical content.
- Safety claims are design-level only. No physical kit was available for verification; this is stated in `docs/SAFETY.md` §8 and must stay in the report.
- All documents are written in plain English.