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
3. Ladder program with rung comments — `src/`
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
docs/ARCHITECTURE.md   layout, I/O map, state machine, ladder structure, HMI map
docs/SAFETY.md         E-Stop, interlocks, safety matrix, limitations
docs/TEST-PLAN.md      TC-01 … TC-34
docs/DECISIONS.md      every grill answer + the one open question
src/                   OpenPLC program (exported project + LAD source)
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
- **P2 Plant simulator** — `sim/plant.py` plus `sim/scenarios/acceptance-10.json`. Single-box runs (small / large / reject), E-Stop and the two-step reset are verified.
- **P3 Program** — Structured Text state machine in `src/conveyor-sorter/conveyor-sorter/pous/programs/main.st`. Compiles, uploads and runs headlessly.
- **Acceptance run (TC-13, 10 boxes)** — **not green yet.** The counters do reach the expected 4 small / 3 large / 3 reject, but individual boxes still land in the wrong lane: a box whose size reading is uncertain passes straight instead of being rejected. The fix is in progress; the documentation is deliberately ahead of the code, and the gap is tracked in `TODO.md`.
- No hardware has ever been connected. Every safety claim in `docs/SAFETY.md` is design-level only.

## Notes

- The PDF is a 17-slide Canva deck; only the "Project 4" slide carries technical content.
- Safety claims are design-level only. No physical kit was available for verification; this is stated in `docs/SAFETY.md` §8 and must stay in the report.
- All documents are written in plain English.