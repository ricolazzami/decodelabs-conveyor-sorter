# TEST PLAN — acceptance tests (FAT)

Stack: OpenPLC Runtime v4 + scripted plant simulator (Modbus TCP) + FUXA. Every case traces to `REQUIREMENTS.md`.
Evidence required: simulator log line, screenshot, or video frame. "Passed by inspection" is not accepted for the safety cases.

Tags per `ARCHITECTURE.md` §2–3.

## A. Startup and I/O

| ID | Req | Pre | Action | Expected |
|----|-----|-----|--------|----------|
| TC-01 | IO-01 | Program loaded | Compare tag list with I/O map | 1:1, no orphan tag |
| TC-02 | NFR-06 | Cold start, E-Stop out, both pushers home | Start runtime | State = IDLE, all outputs off, no fault |
| TC-03 | FR-06 | IDLE | Press Start | READY → RUNNING, `MOTOR_RUN` = 1 |
| TC-04 | FR-06 | RUNNING | Press Stop | STOPPING → IDLE, `MOTOR_RUN` = 0 |
| TC-05 | SR-04 | IDLE, E-Stop pressed | Press Start | Does not start |
| TC-06 | IO-03 | Any | Power up with no command | Both valves 0, motor 0 |

## B. Sorting

| ID | Req | Pre | Action | Expected |
|----|-----|-----|--------|----------|
| TC-07 | FR-01, FR-02 | RUNNING | Feed a short box (S1_SIZE = 0) | No valve fires; counted SMALL; leaves at lane A |
| TC-08 | FR-01, FR-02 | RUNNING | Feed a tall box (S1_SIZE = 1) | `P1_VALVE` = 1 only in P1_DIVERT, retracts, counted LARGE (lane B) |
| TC-09 | FR-02, FR-08 | RUNNING | Feed a box with no valid size reading (timeout) | Routed to lane C by pusher 2, counted REJECT, amber lamp pulses, line keeps running |
| TC-10 | FR-03 | RUNNING | Pulse S1_BOX for 10 ms | Detected once, no double count, no missed box |
| TC-11 | FR-05 | P1_DIVERT | Box leaves station 1 | Valve off, P1_RETURN, then RUNNING after `P1_HOME` |
| TC-12 | FR-05 | P2_DIVERT | Box leaves station 2 | Valve off, P2_RETURN, then RUNNING after `P2_HOME` |
| TC-13 | FR-02, FR-10 | RUNNING | Feed 10 boxes: 4 small, 3 large, 3 forced-reject | Counts = 4/3/3, zero mis-sorts (NFR-04) |
| TC-14 | FR-03 | RUNNING | Box at station 1, then immediately a second box | Second box not lost; class memory correct (single-box assumption honoured) |

## C. Safety interlocks (graded)

| ID | Req | Pre | Action | Expected |
|----|-----|-----|--------|----------|
| TC-15 | SR-01, SR-02 | RUNNING, motor on | Press E-Stop | Motor drops (hardware), state = ESTOP, both valves 0, red lamp on, sequence frozen |
| TC-16 | SR-01 | Each reachable state in turn (IDLE, RUNNING, P1_DIVERT, P1_RETURN, P2_DIVERT, P2_RETURN, FAULT) | Press E-Stop in each | Same result in all 7 states: ESTOP, motor off, both valves off (STOPPING lasts one scan; ESTOP is the destination, not a source) |
| TC-17 | SR-03 | E-Stop pressed, `MOTOR_RUN` forced 1 | Observe motor | Stays off — hardware independence (BLOCKED without kit: reported with wiring-diagram design evidence) |
| TC-18 | SR-04, FR-12 | ESTOP | Release E-Stop only | Stays ESTOP, motor off, no restart |
| TC-19 | SR-04, FR-12 | ESTOP, E-Stop released | Press Reset | Latch clears, state = IDLE (not RUNNING) |
| TC-20 | FR-12 | After TC-19 | Press Start | Line runs — proves two-step recovery |
| TC-21 | SR-06 | RUNNING, no box at station 1 | Hold a stale station-1 record with the box gone | Valve never energizes (quiet line and stale record); stale record raises fault 8 |
| TC-22 | SR-07 | P1_DIVERT | Never assert `P1_EXT` | After `T_extend`: valve off, FAULT, motor off |
| TC-23 | SR-07 | P2_RETURN | Never assert `P2_HOME` | After `T_return`: FAULT, motor off |
| TC-24 | SR-05 | RUNNING | Open `MOTOR_OL` | FAULT, motor off, Reset required |
| TC-25 | SR-08 | FAULT latched | Use FUXA to write a reset coil | State stays FAULT — HMI cannot clear a safety latch |
| TC-26 | SR-02 | P1_DIVERT and P2_DIVERT (separately) | Press E-Stop | Both valves de-energize, both cylinders return |

## D. HMI (FUXA)

| ID | Req | Pre | Action | Expected |
|----|-----|-----|--------|----------|
| TC-27 | FR-11 | FUXA connected on Modbus TCP | Observe screen in RUNNING | Motor/state/counters match runtime |
| TC-28 | FR-11, FR-10 | Feed 3 boxes | Compare HMI counters with runtime | Equal, update ≤ 1 s |
| TC-29 | FR-11, SR-08 | Disconnect FUXA, then press physical Reset | Line recovers | PLC keeps running; HMI loss does not stop or restart anything |
| TC-30 | SR-08 | FUXA only, no physical buttons | Try to start the line from HMI | Allowed only for Start/Stop (documented), never for safety reset |

## E. Reproducibility and portability

| ID | Req | Pre | Action | Expected |
|----|-----|-----|--------|----------|
| TC-31 | NFR-07 | Simulator script present | Run the scripted test sequence | Same results as manual run, log saved |
| TC-32 | NFR-01 | RUNNING | S2 leaves while S1 arrives in the same scan | No state skip, no valve overlap |
| TC-33 | NFR-02 | Any | Open program printout | Every rung commented, every tag described |
| TC-34 | NFR-03 | Program exported | Inspect for vendor-only instructions | Only contacts, coils, TON, CTU/DINT maths |

## F. Exit criteria

- TC-01 … TC-34 pass, or a failure has documented cause + fix + retest entry.
- TC-15 … TC-26 cannot be waived: they are the graded safety evidence.
- Test log written to `docs/TEST-LOG.md` with date, result and evidence path per case.
- Video: normal 10-box cycle, E-Stop mid-divert, two-step recovery, HMI screen.