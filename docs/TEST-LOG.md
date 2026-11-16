
# TEST LOG — acceptance test results

Evidence for `docs/TEST-PLAN.md`. Each section is one run of `sim/tests.py`
against the live OpenPLC runtime; a section is appended, never overwritten, so
the history is kept. The scenario-level runs (`sim/run-scenarios.sh`, TC-13 and
the fault cases) write their own logs to `sim/logs/` (not committed).

Re-run:

```bash
.venv/bin/python sim/tests.py          # TC-01 ... TC-14
.venv/bin/python sim/tests.py TC-10    # one case
bash sim/run-scenarios.sh              # the scenario suite, clean state each time
```

## Run 2026-11-17 00:42 — sim/tests.py

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

14/14 passed.
