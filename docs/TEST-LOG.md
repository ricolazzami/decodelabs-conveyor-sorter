
# TEST LOG — conveyor box sorter

Results are appended, newest last. Each block is one run of `sim/tests.py`
(TC-01…TC-26) or `sim/run-scenarios.sh`. A `BLOCKED` case is one that cannot be
executed without physical hardware — it is reported, not passed. See
`docs/TEST-PLAN.md` for what each case is meant to prove and `docs/SAFETY.md` §7
for why TC-17 is blocked.

## Run 2026-11-17 01:51 — sim/tests.py

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

25/26 passed, 1 blocked (no hardware), 0 failed.
