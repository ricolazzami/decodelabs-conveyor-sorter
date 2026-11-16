# SAFETY — interlock design and E-Stop

> **Read before wiring anything.** This is a training-kit design that follows industrial safety principles. It is **not** a certified safety function and has not been assessed to ISO 13849 / IEC 62061. For a real machine with people near it, a qualified engineer must review it and a proper safety relay with dual channels must be used. Never rely on PLC logic alone to stop a drive.

## 1. What the brief demands

> "Implement strict safety interlocks (e.g., E-Stop logic halts all motors immediately)."

Three things must be true at the same time:

1. The stop happens **immediately** — not after the rung finishes.
2. The stop works **even if the PLC is hung, looping, or has a bad input**.
3. The machine **cannot restart by itself** when the button is released.

Only a hardwired circuit can promise (2). So the design is layered.

## 2. Layer 1 — hardwired stop (the real safety function)

```
 mains ──[ Q1 motor protection ]──[ K1 main contacts ]──► M conveyor motor
 coil circuit (24 VDC):
 +24V ──[ E-Stop NC, latching ]──[ MOTOR_OL NC ]──[ K1 aux NC ]──( K1 coil )── 0V
                                              ▲
                              [ PLC MOTOR_RUN relay contact ]
```

- E-Stop is a latching mushroom, **NC**, twist or key release (ISO 13850 style).
- It sits **in series with the contactor coil**, so pressing it physically drops K1. Motor power goes away regardless of PLC state.
- Higher category version: safety relay (e.g. Pilz PNOZ, SICK UE10 class), two NC contacts in, relay contacts in the coil circuit, one aux contact back to the PLC as `E_STOP_OK`.
- The PLC output can never energize the motor by itself — it must pass through the E-Stop contact.
- **Both pusher valves are de-energized by the E-Stop** as well (valve supply on the same switched 24 V rail, or through the K1 aux contact). Spring return brings both cylinders home.

## 3. Layer 2 — PLC logic

The PLC reads `E_STOP_OK` (NC, 1 = healthy) and does the bookkeeping:

- Enter `ESTOP` from any state.
- Force `P1_VALVE = 0` and `P2_VALVE = 0`.
- Latch `reset_required`.
- Recovery requires **E-Stop released AND Reset pressed**, then a separate **Start**. Releasing the button alone must not resume.
- The web HMI (FUXA) may display the state, but it cannot clear `reset_required` — only the physical Reset input can (SR-08).

## 4. Layer 3 — fault interlocks

| Code | Fault | Detection | Reaction |
|------|-------|-----------|----------|
| 1 | E-Stop pressed | `E_STOP_OK` = 0 | `ESTOP`, motor and both valves off (motor already dropped in hardware), `reset_required` |
| 6 | Motor overload / drive fault | `MOTOR_OL` = 0 | `FAULT`, motor off, both valves off, Reset required |
| 2 | Pusher 1 not extended | `P1_EXT` still 0 after `T_extend` | `P1_VALVE` off, `FAULT` |
| 3 | Pusher 1 not home | `P1_HOME` still 0 after `T_return` | `FAULT`, motor off (possible jam) |
| 4 | Pusher 2 not extended | `P2_EXT` still 0 after `T_extend` | `P2_VALVE` off, `FAULT` |
| 5 | Pusher 2 not home | `P2_HOME` still 0 after `T_return` | `FAULT`, motor off |
| 7 | Box stuck between stations | `S1_BOX` **and** `S2_BOX` both true for 3 s | `FAULT`, motor off |
| 8 | Pusher record with no box | `p1_queued` > 0 but `S1_BOX` false (or `s2_pending` but `S2_BOX` false) | Valve never energizes, `FAULT` (SR-06) |
| — | Unclassified box | `S1_SIZE` missing or contradictory at classify time | Route to REJECT + amber lamp (FR-08), count it — **not** a fault |

Unclassified boxes are **not** a fault: the reject lane exists for them. They are counted and indicated, never hidden.

Fault 8 is the SR-06 guard. Because the program only ever strokes a pusher while the
box it was measured for is still in the zone, a record that outlives its box (the box
was lifted off by hand, or it outran the sequence) is caught: nothing is stroked into
air, and the box is not allowed to slip down the SMALL lane as if it had been checked.
Faults latch the sequence into `FAULT`; only a physical Reset with the cause cleared,
then a separate Start, brings it back (the HMI has no reset path — SR-08).

## 5. State-to-output safety matrix

| State | `MOTOR_RUN` | `P1_VALVE` | `P2_VALVE` | Safe? |
|-------|-------------|------------|------------|-------|
| 0 IDLE | 0 | 0 | 0 | yes |
| 1 READY | 0 | 0 | 0 | yes |
| 2 RUNNING | 1 | 0 | 0 | yes |
| 3 *(reserved)* | 1 | 0 | 0 | yes — classification runs every scan, outside the CASE |
| 4 P1_DIVERT | 1 | 1 | 0 | allowed — box at station 1 only |
| 5 P1_RETURN | 1 | 0 | 0 | yes |
| 6 P2_DIVERT | 1 | 0 | 1 | allowed — box at station 2 only |
| 7 P2_RETURN | 1 | 0 | 0 | yes |
| 8 STOPPING | 0 | 0 | 0 | yes |
| 9 FAULT | 0 | 0 | 0 | yes |
| 10 ESTOP | 0 (hardware) | 0 | 0 | yes |

Rule: valve 1 only in `P1_DIVERT`, valve 2 only in `P2_DIVERT`. Two valves on at once is a defect. The program enforces this twice — the state mapping and a hard guard that also forces all three outputs off whenever `safety_ok` is false.

## 6. Mechanical and operational guards (state them, do not fake them)

- Fixed guard or light curtain at both divert stations on a real line — not modelled in logic.
- Pinch points at both cylinders: hands out of stroke, machine labelled.
- Lockout/tagout before maintenance.
- Two-hand or hold-to-run control is **not** implemented — note it as a gap if people work near the line.

## 7. Wiring verification (before logic testing)

1. Disconnect PLC `MOTOR_RUN`. Press E-Stop. K1 drops, motor cannot start. ✔
2. Release E-Stop. Motor does **not** start (no seal, no auto-run). ✔
3. Force `MOTOR_RUN` = 1 with E-Stop pressed. Motor stays off. ✔ — proves hardware independence
4. Force `MOTOR_RUN` = 1 with `MOTOR_OL` open. Motor stays off. ✔
5. Press E-Stop while both cylinders are extended (simulate): both valves drop, both cylinders return. ✔
6. `E_STOP_OK` reads 1 healthy, 0 pressed (NC not double-inverted in software). ✔

Evidence: photo of the coil circuit + short video of test 3.

**Current status: tests 1–6 are design-only.** No physical kit is available. The report must say so.

## 8. Honest limitations

- Single channel, no safety relay, no monitored reset → Category B/1 at best.
- No risk assessment to ISO 12100 performed.
- Simulation proves logic behaviour only, never hardware safety.
- Web HMI is not part of any safety function.
- Stating these limits is stronger than claiming certification.