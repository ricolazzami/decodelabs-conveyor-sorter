# Plant simulator (P2)

The test harness written **before** the PLC program. The PLC is the Modbus TCP
server; this simulator is the plant. It writes the sensor and button bits, reads
the actuator bits and the HMI words, moves boxes, works the cylinders, and
decides whether the run did what it promised.

## Run it

```bash
.venv/bin/python sim/plant.py --scenario sim/scenarios/acceptance-10.json
bash sim/run-scenarios.sh            # every scenario, clean PLC state each time
bash sim/run-scenarios.sh 'fault-*'  # only the fault-injection ones
```

`run-scenarios.sh` needs the runtime up (`docker compose up -d`) and `.env`
filled in. It stops and starts the PLC before each scenario, which is what
reloads the program and therefore resets the counters — the program never clears
its own counters on purpose.

Logs: `sim/logs/<scenario>.log` (full run) and `<scenario>.out` (stderr, so a
silent failure is visible). `sim/run.log` is the default single-run path.

## What is modelled

| Part | Detail |
| --- | --- |
| Belt | 0.3 m/s, boxes enter on a 400 mm pitch, 50 ms tick |
| Boxes | 200 mm long, small (< 100 mm) or large (>= 100 mm) |
| Station 1 | 600 mm, presence eye + height eye at 60 mm, pusher 1 |
| Station 2 | 1200 mm, presence eye, pusher 2 (reject lane) |
| Cylinders | 250 ms travel, both home and extended sensors modelled |
| Lane A | straight past station 2, no actuator ever fires |

## Fault injection

Set under `"faults"` in a scenario file. A value is either `true` or a time in
seconds, so the fault can land mid-run.

| Flag | Effect | PLC reaction expected |
| --- | --- | --- |
| `estop_at_s` | E-Stop contact opens | `state 10`, fault 1, both valves off |
| `overload_at_s` | overload contact opens (NC) | `state 9`, fault 6, belt stopped |
| `cylinder_jam: {p1\|p2: "cannot_extend"}` | cylinder never reaches the extended sensor | extend timeout, fault 2 (P1) / 4 (P2) |
| `cylinder_jam: {p1\|p2: "cannot_return"}` | cylinder stays out | return timeout, fault 3 (P1) / 5 (P2) |
| `s1_eye_dead_at_s` | station 1 presence eye dies | no box is classified, so no box may be guessed SMALL: unseen boxes must be rejected |

Two more faults are proved by `sim/tests.py` rather than by a scenario file, because
they need to be injected at an exact moment that a timeline cannot pin down:

- **Both stations occupied for 3 s → fault 7.** A box square over both eyes trips the
  process monitor.
- **A pusher record with no box → fault 8 (SR-06).** The scenario holds a stale
  station-1 record with the box already gone and proves the valve never energizes.
- **E-Stop pressed mid-state.** `Plant.force_estop()` drops the E-Stop between ticks,
  which is how TC-15, TC-16 and TC-26 press the mushroom from a chosen state.

## Scenario file

```jsonc
{
  "name": "acceptance-10",
  "boxes": [{ "id": 1, "size": "small" },
            { "id": 5, "size": "large", "uncertain": true },
            { "id": 6, "size": "small", "expect_lane": "reject" }],
  "expect_counts": { "count_small": 4, "count_large": 3, "count_reject": 3 },
  "faults": { "estop_at_s": 6.0 },
  "expect": { "state": 9, "fault_code": 5, "actuators_off": true },
  "plant": { "start_delay_s": 1.0 }
}
```

- `uncertain` — the height eye asserts at the edge of the window and has dropped
  by the time the second sample is taken. Not trustworthy, so the box must be
  rejected, never guessed.
- `expect_lane` — the scenario declares the safe outcome when the physical truth
  cannot be the expectation (a dead eye, for example).
- `expect` / `expect_counts` — checked against the PLC words at the end. When a
  fault is expected the run keeps ticking after the last box has landed, because
  the fault is the thing being waited for.
- Without `expect`, the verdict is lane-by-lane against each box's expectation.

## Result semantics

`RESULT: PASS` on the last line means the PLC did what the scenario promised.
The box table above it always prints, so a failing run still shows where each box
went.

## Test runner (TC-01 … TC-26)

`sim/tests.py` runs the test plan's startup and sorting cases against the live
PLC and writes the results to `docs/TEST-LOG.md`:

```bash
.venv/bin/python sim/tests.py          # every case, clean PLC state before each
.venv/bin/python sim/tests.py TC-10    # one case
.venv/bin/python sim/tests.py TC-15 TC-26
```

It reloads the program (stop/start, which is what clears the counters) before
each case, reads the plant the same way `plant.py` does, and prints one line per
case plus the evidence string that goes into the log. TC-13 shells out to the
scenario runner, so a green log means both entry points agree.

Each case prints one of three verdicts:

- `PASS` — the promise held.
- `FAIL` — it did not; the evidence string says what was seen.
- `BLOCKED` — the case cannot be executed here (TC-17 needs physical hardware to
  prove the E-Stop is independent of the PLC). It is not counted as a pass: the
  report states it as blocked, with the design evidence only.

The cases that need the sequence to finish after a single stray input — TC-10 in
particular — drive an **empty** plant so the cylinders still report their reed
switches. Without that feedback the state machine would sit in `P1_DIVERT`
forever, which is correct behaviour but useless as a test of counting.

## Current status

`acceptance-10` passes: every box in its expected lane, counters 4 / 3 / 3, no
fault. The five fault scenarios pass as well.

Full suite (`sim/tests.py`, through P4): **25 PASS, 1 BLOCKED (TC-17, no kit), 0 FAIL**.

Two harness bugs it exposed, both fixed:

- the input image is now written in a single Modbus request (FC15). Two separate
  coil writes could be caught by a PLC scan in between, and the controller would
  then latch a size reading that did not belong to the box it had just detected.
- the classification decides from a sample latched at the rising edge plus a
  second sample 200 ms later, and an IEC timer only updates `Q` when the function
  block is executed — so the timer is called on every scan.
