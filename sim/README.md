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

## Current status (honest)

`acceptance-10` **fails**: the counters reach 4 / 3 / 3 but individual boxes land
in the wrong lane (a box with an untrustworthy reading passes straight instead of
being rejected). That is a program bug in the classification path, not a harness
bug, and it is tracked in `TODO.md` (P3, then P6). The five fault scenarios pass.
