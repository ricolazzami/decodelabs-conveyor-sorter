# ARCHITECTURE — I/O map, sequence, state machine

Platform: **OpenPLC Editor v4 + Runtime v4**, program written as an IEC 61131-3 **Structured Text state machine** (the brief allows ladder *or* a state machine). HMI: **FUXA** (web, Modbus TCP).

Addressing note: this build is a soft PLC with no fieldbus card, so the I/O image is delivered over Modbus TCP by the project's own Modbus server. Digital inputs therefore live in `%MX` (memory bits the plant simulator writes) and outputs in `%QX`. On real hardware with an input card the input column becomes `%IX` — the tags and the logic do not change. Coil offsets: `%QX0.0` = coil 0, `%MX0.0` = coil 64.

## 1. Layout (3 lanes, 2 pushers, 2 stations in series)

```
 box in ──►  conveyor  ──► [ Station 1: S1_BOX + S1_SIZE ] ──► [ Station 2: S2_BOX ] ──► end
                                    │                                │
                              Pusher 1 (P1)                    Pusher 2 (P2)
                                    │                                │
                             LARGE lane (B)                  REJECT lane (C)

 Straight past both stations = SMALL lane (A)   ← de-energized, fail-safe default
```

Why two stations: pusher 1 and pusher 2 would collide if they shared one zone. Station 1 handles LARGE, station 2 (downstream) handles REJECT. A SMALL box triggers neither.

Single-box assumption: only one box is between the two stations at a time. A box at station 1 stays in the `q_class` FIFO (arrival order) until station 2 serves it, so its size can be looked up when it arrives downstream.

## 2. Digital inputs (14 used, 16-point card recommended)

| Tag | Addr | Type | Device | Healthy / at-rest | Purpose |
|-----|------|------|--------|-------------------|---------|
| `E_STOP_OK` | %MX0.0 | NC, 24 V | E-Stop mushroom, latching twist release | **1 = healthy** | Safety feedback (motor also cut in hardware, see SAFETY.md) |
| `PB_START` | %MX0.1 | NO | Green pushbutton | 0 | Start |
| `PB_STOP` | %MX0.2 | NC | Red pushbutton | 1 | Controlled stop |
| `PB_RESET` | %MX0.3 | NO | Blue/black pushbutton | 0 | Clears FAULT / ESTOP latch only — does not start |
| `S1_BOX` | %MX0.4 | PNP | Photo-eye, station 1 entry | 0 | Box at station 1 |
| `S1_SIZE` | %MX0.5 | PNP | Photo-eye at height 60 mm | 0 | 1 = tall (≥ 100 mm) = LARGE |
| `S2_BOX` | %MX0.6 | PNP | Photo-eye, station 2 entry | 0 | Box at station 2 |
| `P1_HOME` | %MX0.7 | PNP | Reed, cylinder 1 retracted | 1 | Pusher 1 safe position |
| `P1_EXT` | %MX1.0 | PNP | Reed, cylinder 1 extended | 0 | Pusher 1 reached end |
| `P2_HOME` | %MX1.1 | PNP | Reed, cylinder 2 retracted | 1 | Pusher 2 safe position |
| `P2_EXT` | %MX1.2 | PNP | Reed, cylinder 2 extended | 0 | Pusher 2 reached end |
| `MOTOR_OL` | %MX1.3 | NC | Thermal overload / drive fault | 1 = healthy | Fault input |
| `MODE_AUTO` | %MX1.4 | NO | Selector, auto/manual | 0 | Simulation keeps this forced 1 (single mode) |
| `S2_CLEAR` | %MX1.5 | PNP | Photo-eye, station 2 exit | 0 | Confirms box left station 2 |
| *(spare)* | %MX1.6–1.7 | — | — | — | Reserved |

If the PLC card has only 8 DI, drop `MODE_AUTO` and `S2_CLEAR` and use timers instead — record the substitution in the report.

## 3. Digital outputs (6 used)

| Tag | Addr | Load | De-energized (safe) | Purpose |
|-----|------|------|---------------------|---------|
| `MOTOR_RUN` | %QX0.0 | Contactor K1 coil (or VFD enable) | Motor off | Conveyor — also in series with E-Stop hardware contact |
| `P1_VALVE` | %QX0.1 | 5/2 solenoid Y1 | Retracted | Divert LARGE (lane B) |
| `P2_VALVE` | %QX0.2 | 5/2 solenoid Y2 | Retracted | Divert REJECT (lane C) |
| `LAMP_RUN` | %QX0.3 | Green stack light | Off | Running |
| `LAMP_FAULT` | %QX0.4 | Red stack light | Off | Fault / E-Stop |
| `LAMP_REJ` | %QX0.5 | Amber stack light | Off | A box went to reject (pulses 1 s) |

Counters and state are exposed to FUXA over Modbus TCP as holding registers (see §9), not as outputs.

## 4. Sequence

```
classification (every scan, outside the CASE):
  S1_BOX ↑        latch size_sample := S1_SIZE, start T_settle
  200 ms later    both samples equal?  tall  → p1_queued++      (LARGE)
                                        short → FIFO ← SMALL
                  samples differ → FIFO ← REJECT
  S1_BOX ↓ before it settled        → FIFO ← REJECT

state machine:
IDLE ──(START ∧ E_STOP_OK ∧ MOTOR_OL ∧ P1_HOME ∧ P2_HOME ∧ ¬reset_required ∧ MODE_AUTO)──► READY
READY ──(clear the tracking, never the counters)──► RUNNING     MOTOR_RUN = 1
RUNNING ──(STOP ∨ ¬safety)──► STOPPING
        ──(p1_queued > 0 ∧ P1_HOME ∧ S1_BOX)──► P1_DIVERT   ← the box it was measured
        ──(s2_pending ∧ S2_BOX)──► pop FIFO                  for is still in the zone
                                   SMALL ──► RUNNING, CNT_SMALL++
                                   REJECT / empty FIFO ──► P2_DIVERT
        ──(p1_queued > 0 ∧ P1_HOME ∧ ¬S1_BOX)──► FAULT 8    ← record, but no box to push
        ──(s2_pending ∧ ¬S2_BOX)──► FAULT 8                  ← left station 2 unserved
P1_DIVERT  : P1_VALVE = 1 ; T_extend → FAULT 2
P1_RETURN  : P1_VALVE = 0 ; exit on P1_HOME ; T_return → FAULT 3 ; CNT_LARGE++
P2_DIVERT  : P2_VALVE = 1 ; T_extend → FAULT 4
P2_RETURN  : P2_VALVE = 0 ; exit on P2_HOME ; T_return → FAULT 5 ; CNT_REJECT++
             and the amber lamp pulses for T_reject_lamp
any state ──(STOP)──► STOPPING : outputs off ──► IDLE
any state ──(¬E_STOP_OK)──► ESTOP 1 ; ──(¬MOTOR_OL)──► FAULT 6
box at both stations for 3 s ──► FAULT 7
FAULT / ESTOP ──(clear the tracking, keep the counters, reset the guard timers)
```

Two layers, on purpose:

1. **Classification** runs on every scan, outside the state machine. A box at station 1 is read at the rising edge and again 200 ms later; the two readings decide `SMALL`, `LARGE` or `REJECT`. It cannot be deferred to the sequencer, because the box is only under the sensor for about one second - a busy actuator sequence would otherwise sample air and turn a good box into "unclassified".
2. **The actuator sequence** is the state machine. `LARGE` boxes are counted in `p1_queued` and diverted at station 1, so they never reach station 2 and are deliberately kept out of the station-2 FIFO. Everything else (SMALL, REJECT) goes into that FIFO in arrival order and is decided at station 2: SMALL straight through, REJECT through pusher 2.

`RUNNING` serves **station 1 first**, because that box is only inside the pusher zone for about a second, while an arrival at station 2 is latched (`s2_pending`) and can wait without being lost.

**SR-06 - a pusher only strokes while its box is in the zone.** A pending station-1 box is diverted only when `S1_BOX` is still true. If the record exists but the box has gone (lifted off by hand, or it outran the sequence) the PLC raises **fault 8** rather than stroking into air or letting the box slip down the SMALL lane silently. The same rule covers a station-2 box that leaves the zone before it is served.
## 5. State table

| # | State | MOTOR | P1 | P2 | Exit | Fault traps |
|---|-------|-------|----|----|------|-------------|
| 0 | `IDLE` | 0 | 0 | 0 | START + all healthy | — |
| 1 | `READY` | 0 | 0 | 0 | run seal set | — |
| 2 | `RUNNING` | 1 | 0 | 0 | p1 zone occupied, S2 box↑ (latched) & occupied, STOP, fault, SR-06 fault 8 | — |
| 3 | *(reserved)* | 1 | 0 | 0 | unused — classification runs every scan, outside the CASE | — |
| 4 | `P1_DIVERT` | 1 | 1 | 0 | P1_EXT | T_extend |
| 5 | `P1_RETURN` | 1 | 0 | 0 | P1_HOME (or nothing to return) | T_return |
| 6 | `P2_DIVERT` | 1 | 0 | 1 | P2_EXT | T_extend |
| 7 | `P2_RETURN` | 1 | 0 | 0 | P2_HOME | T_return |
| 8 | `STOPPING` | 0 | 0 | 0 | always → IDLE | — |
| 9 | `FAULT` | 0 | 0 | 0 | RESET after cause cleared | — |
| 10 | `ESTOP` | 0 (hardware) | 0 | 0 | RESET **and** E_STOP_OK | — |

Rule: **`P1_VALVE` may be 1 only in `P1_DIVERT`; `P2_VALVE` only in `P2_DIVERT`.** Any other combination is a defect.

`ESTOP` is entered from every state. The motor is already de-energized by hardware; the state exists so the sequence cannot resume by itself.

## 6. Program structure (Structured Text)

| Group | Content |
|-------|---------|
| G1 Safety & enable | E_STOP_OK normalisation, MOTOR_OL, run seal, `reset_required` latch |
| G2 State machine | one `CASE` branch per state, transition guards, the station-2 FIFO and `p1_queued` |
| G3 Outputs | state → MOTOR_RUN / P1_VALVE / P2_VALVE / lamps (one coil per rung) |
| G4 Timers & faults | T_settle, T_extend(×2), T_return(×2), T_reject_lamp, fault latch + cause word |
| G5 Counters | CNT_SMALL, CNT_LARGE, CNT_REJECT in `%QW1..%QW3` |

Rules applied everywhere:
- Seal-in for RUN and FAULT, never a bare momentary.
- Interlock first in the rung, action last — reviewer reads cause before effect.
- One coil per tag in the whole program.
- Timers reset by their own state exit, not by first-scan.

## 7. Classifier (single place to change)

```
IF S1_BOX rising     THEN size_sample := S1_SIZE ; s1_pending := TRUE
IF s1_pending AND T_settle.Q THEN
    IF S1_SIZE <> size_sample THEN REJECT   (* two readings disagree *)
    ELSIF size_sample         THEN LARGE
    ELSE                           SMALL
IF s1_pending AND NOT S1_BOX  THEN REJECT   (* left before the reading settled *)
```

Two samples 200 ms apart, because that is roughly the time a 200 mm box takes to
cross a 60 mm-high beam at 0.3 m/s. A single sample at one instant could catch the
box's leading chamfer or a reflection and call a tall box short.

Swapping to colour touches this block only: the height eye at 60 mm becomes a
colour sensor, and the rest of the program is unchanged.

## 8. Timing constants (simulation values — confirm at bench)

| Name | Value | Reason |
|------|-------|--------|
| `T_settle` | 200 ms | second sample, sensor settle |
| `T_extend` | 800 ms | cylinder stroke guard |
| `T_return` | 1000 ms | spring-return guard |
| `T_reject_lamp` | 1000 ms | amber pulse |
| *(no `T_debounce`)* | — | dropped on purpose, see below |
| box pitch (sim) | 400 mm | plant model |
| belt speed (sim) | 0.3 m/s | ⇒ ~45 boxes/min theoretical |

`T_debounce` was in the first draft and is deliberately not implemented. Two
reasons:

1. The scan is 20 ms, so a contact that chatters for less than one scan is not
   seen at all, and a chatter that spans a scan boundary looks like a genuine
   edge. A software debounce would therefore only help for chatter of 20–200 ms.
2. A box blocks the beam for about 1 s. The 200 ms two-sample classification is
   already a much longer filter than any debounce would be, and it rejects a box
   whose reading is not stable instead of guessing.

What this does **not** cover, and is documented as a limitation: if a box's beam
is interrupted and re-made inside the 200 ms window (a real gap in the box, or
electrical noise), the classifier sees two boxes. On a real machine the fix is a
hardware debounce or a light curtain with a clean output — not more PLC code.

## 9. HMI — FUXA over Modbus TCP

OpenPLC Runtime v4 exposes the `modbus_slave` plugin (TCP, port 502). FUXA connects as Modbus TCP client.

| FUXA element | Modbus | OpenPLC tag |
|--------------|--------|-------------|
| Motor lamp | coil 0 | `MOTOR_RUN` |
| P1 coil / P2 coil | coils 1, 2 | `P1_VALVE`, `P2_VALVE` |
| Stack light trio | coils 3–5 | `LAMP_RUN/FAULT/REJECT` |
| Start / Stop | coils 80–81 (write) | `HMI_START` `%MX2.0`, `HMI_STOP` `%MX2.1` (never Reset — SR-08) |
| State number | holding register 0 | `ST_WORD` (`%QW0`) |
| Counters | holding registers 1–3 | `CNT_SMALL`, `CNT_LARGE`, `CNT_REJECT` (`%QW1..%QW3`) |
| Fault cause word | holding register 4 | `FAULT_CODE` (`%QW4`) |

### 9.1 Fault codes (`FAULT_CODE`, holding register 4)

| Code | Meaning | Set by |
|------|---------|--------|
| 0 | No fault | Reset with the cause cleared |
| 1 | E-Stop pressed | `E_STOP_OK` = 0 (supervisory block) |
| 2 | Pusher 1 did not extend in time | `T_extend` in `P1_DIVERT` |
| 3 | Pusher 1 did not return in time | `T_return` in `P1_RETURN` |
| 4 | Pusher 2 did not extend in time | `T_extend` in `P2_DIVERT` |
| 5 | Pusher 2 did not return in time | `T_return` in `P2_RETURN` |
| 6 | Motor overload / drive fault | `MOTOR_OL` = 0 (supervisory block) |
| 7 | Both stations occupied for over 3 s | `t_s2_stuck` |
| 8 | No box in the pusher's zone for a record that exists (SR-06) | `RUNNING` |

A fault latches the sequence into `FAULT` (9) or `ESTOP` (10). The operator must
clear the cause and press the physical Reset, then Start — the HMI has no reset
path (SR-08). `docs/SAFETY.md` §4 lists the same codes with the safety rationale.

Keep a physical button path too — FUXA is an indicator/extra control, not the only way to stop the line. A web HMI is not a safety device.

### 9.2 The screen that was actually built

`fuxa/build.py` generates `fuxa/project.fuxap` (one view, 13 tags) and `--apply`
POSTs it to the running FUXA; `docs/img/fuxa.png` is the rendered screen
(counters 4 / 3 / 3, state RUNNING, fault none).

What the screen shows: conveyor motor, RUN / FAULT / REJECT lamps, the state word
as text, the fault word as text, the three counters, and a START / STOP pair. It
has no reset control at all (SR-08). Start and Stop are momentary: mousedown sets
the coil, mouseup clears it.

Four details cost time and are worth writing down:

- **The stock `frangoteam/fuxa` image has no `modbus-serial`**, so a Modbus TCP
  device fails with `try to create PLC but plugin is missing!`. `fuxa/Dockerfile`
  adds it, and `docker-compose.yml` builds the HMI from there
  (`conveyor-sorter-fuxa:latest`).
- **Tag ids carry the device prefix** (`PLC^~^CNT_SMALL`). The web client stores
  received values under the id the device sends and binds a widget by
  `variableId`, so a tag id without the prefix renders a screen that never moves.
- **The device polls every 1000 ms** (`"polling": 1000` on the device); FUXA's
  default is 3 s, which would break the one-second refresh promised in the test
  plan.
- **`svg-ext-motor` is a legacy shape** the 1.3.6 client no longer ships, so the
  motor is a semaphore lamp like the three stack-light lamps. `svg-ext-value`
  needs its child `<text>`, `svg-ext-html_select` needs a `S-HXS_...` id on the
  `<SELECT>`, and `svg-ext-html_button` a `B-HXB_...` id on the `<BUTTON>`; those
  prefixes are how the client finds the element to drive.

`fuxa/check.py` reads the PLC over Modbus and asks the HMI server what it is
serving, which is TC-27/TC-28; `fuxa/shot.py` takes the screenshot after the
Angular app has painted.

## 10. Diagrams

- `docs/img/wiring.png` — power, contactor, E-Stop, valve supply, PLC I/O — **done** (P4)
- `docs/img/layout.png` — 3 lanes, 2 stations — to produce (docs)
- `docs/img/state-machine.png` — from §4 — to produce (docs)
- `docs/img/fuxa.png` — HMI screen — **done** (P5)
- Printouts of the program with comments — P7 (the program is Structured Text, not ladder)