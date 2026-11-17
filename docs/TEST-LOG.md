# TEST LOG — conveyor box sorter

One block per full run of `.venv/bin/python sim/tests.py` (TC-01...TC-34), plus
the evidence that does not come out of the runner. A `BLOCKED` case is one that
cannot be executed without physical hardware - it is reported, not passed. See
`docs/TEST-PLAN.md` for what each case proves and `docs/SAFETY.md` §7 for why
TC-17 is blocked.

## Run 2026-11-17 09:50 — sim/tests.py

| Case | Result | Evidence |
|------|--------|----------|
| TC-01 | PASS | 27 located tags; undocumented: none; locatable from the I/O map but missing from the program: none |
| TC-02 | PASS | zeroed image -> state 10 (ESTOP, motor+valves off=True); healthy + Reset -> state 0, all outputs off=True, fault 0 |
| TC-03 | PASS | state 2, MOTOR_RUN=True |
| TC-04 | PASS | after Stop: state 0, MOTOR_RUN=False |
| TC-05 | PASS | state 10 (must stay ESTOP), MOTOR_RUN=False |
| TC-06 | PASS | power-up with no command: {'MOTOR_RUN': False, 'P1_VALVE': False, 'P2_VALVE': False, 'LAMP_RUN': False, 'LAMP_FAULT': True, 'LAMP_REJ': False} |
| TC-07 | PASS | lane=small, count_small=1, valve violations=none |
| TC-08 | PASS | lane=large, count_large=1, states=[2, 4, 5, 2] |
| TC-09 | PASS | lane=reject, count_reject=1, amber=True, final state 2 |
| TC-10 | PASS | one 10 ms edge -> 0 count(s) (0 is allowed, 2 is not); one clean box -> 1 (must be 1); final state 2 |
| TC-11 | PASS | states=[2, 4, 5, 2], count_large=1 |
| TC-12 | PASS | states=[2, 6, 7, 2], count_reject=1 |
| TC-13 | PASS | RESULT: PASS (also covered by sim/run-scenarios.sh) |
| TC-14 | PASS | lanes=['large', 'small'], counts=1/1/0 |
| TC-15 | PASS | states [2, 4, 10] then [10]; fault 1; motor and both valves off; red lamp on; counters frozen at 0/0/0 |
| TC-16 | PASS | E-Stop pressed in states [0, 2, 4, 5, 6, 7, 9] -> ESTOP with motor and both valves off; problems: none |
| TC-17 | BLOCKED | design evidence only: docs/img/wiring.png puts the E-Stop NC contact in series with the K1 coil, so MOTOR_RUN cannot energize the motor while it is open. No physical kit exists (docs/SAFETY.md §7), so this case is not executed and the report must say so. |
| TC-18 | PASS | trip (10, 1, False); after releasing the button alone state 10 with MOTOR_RUN=False - it does not resume by itself |
| TC-19 | PASS | after Reset: state 0 (IDLE, not RUNNING), fault cleared to 0 |
| TC-20 | PASS | release alone: no start; Reset then Start: state 2, MOTOR_RUN=True - two steps, as designed |
| TC-21 | PASS | quiet line, no box: valve never energised=True; record with no box in the zone: valve never energised=True, state 9, fault 8 (8 = no box in the pusher zone) |
| TC-22 | PASS | states [2, 4, 9]; fault 2 (2 = P1 extend timeout); valve off, motor off |
| TC-23 | PASS | states [2, 6, 7, 9]; fault 5 (5 = P2 return timeout); 0 rejected box(es) already counted, motor off |
| TC-24 | PASS | states [2, 9]; trip state 9 fault 6 motor False; Reset with the overload still open: state 9 (blocked); Reset once it cleared -> state 0 |
| TC-25 | PASS | latched in FAULT; HMI Start+Stop written: state 9, fault 6 still standing, motor off. The HMI has no reset coil at all (SR-08), and a Reset via the %MX input path is a simulator artefact, not a field one |
| TC-26 | PASS | state 4: valve was on=True, valves off after the trip=True, both cylinders home=True, Reset+Start recovers=True; state 6: valve was on=True, valves off after the trip=True, both cylinders home=True, Reset+Start recovers=True |
| TC-27 | PASS | HMI matches the runtime |
| TC-28 | PASS | PLC 1/1/1, HMI 1/1/1, matched in 0.61 s |
| TC-29 | PASS | FUXA down 2 s: line stopped and restarted from the panel only (RUNNING=True), counters kept=True; HMI back and agreeing=True |
| TC-30 | PASS | HMI Start from IDLE=True, HMI Stop=True, reset widgets on the screen=0 (must be 0) |
| TC-31 | PASS | 6 scenario(s) run by the script, 6 PASS, 0 log(s) missing or not PASS |
| TC-32 | PASS | same-scan station overlap=10 tick(s), S2-leaves-as-S1-arrives=0 tick(s); states [2, 4, 5, 2] - illegal transitions none; valve problems none; lanes {1: 'small', 2: 'large'}; counters 1/1/0 |
| TC-33 | PASS | 60 declared tags, 0 without a comment; 11 state branches, 0 without a comment |
| TC-34 | PASS | function blocks called: ['t_lamp', 't_p1_ext', 't_p1_ret', 't_p2_ext', 't_p2_ret', 't_s2_stuck', 't_settle']; types: ['ARRAY[0..7] OF INT', 'BOOL', 'INT', 'TON']; vendor names: none |

33/34 passed, 1 blocked (no hardware), 0 failed.

## Video evidence (P6)

`docs/demo.mp4`, recorded with `.venv/bin/python sim/demo.py` against the live
PLC and the FUXA screen, one take:

| Scene | Time | What it shows |
|-------|------|---------------|
| 10-box cycle | 0:00-0:18 | counters climb to 4 SMALL / 3 LARGE / 3 REJECT, no valve violation |
| E-Stop mid-divert | 0:21-0:25 | pusher 1 energized, then state ESTOP, fault "E-Stop pressed", motor and valves off, counters frozen at 4 / 3 / 3 |
| Two-step recovery | 0:26-0:28 | Reset leaves IDLE with the latch cleared, then a separate Start returns to RUNNING; counters kept |
| HMI Stop / HMI Start | 0:28-0:34 | the real screen buttons stop and restart the line |
