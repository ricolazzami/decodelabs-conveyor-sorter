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

Single-box assumption: only one box is between the two stations at a time. The class is held in `box_class` memory between S1 and S2.

## 2. Digital inputs (14 used, 16-point card recommended)

| Tag | Addr | Type | Device | Healthy / at-rest | Purpose |
|-----|------|------|--------|-------------------|---------|
| `E_STOP_OK` | %MX0.0 | NC, 24 V | E-Stop mushroom, latching twist release | **1 = healthy** | Safety feedback (motor also cut in hardware, see SAFETY.md) |
| `PB_START` | %IX0.1 | NO | Green pushbutton | 0 | Start |
| `PB_STOP` | %IX0.2 | NC | Red pushbutton | 1 | Controlled stop |
| `PB_RESET` | %IX0.3 | NO | Blue/black pushbutton | 0 | Clears FAULT / ESTOP latch only — does not start |
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
| `LAMP_REJECT` | %QX0.5 | Amber stack light | Off | A box went to reject (pulses 1 s) |

Counters and state are exposed to FUXA over Modbus TCP as holding registers (see §9), not as outputs.

## 4. Sequence

```
IDLE ──(START ∧ E_STOP_OK ∧ MOTOR_OL ∧ P1_HOME ∧ P2_HOME ∧ ¬reset_required)──► READY
READY ──(run seal)──► RUNNING                              MOTOR_RUN = 1
RUNNING ──(S1_BOX ↑)──► S1_CLASSIFY
S1_CLASSIFY ──(settle 200 ms)──►  tall?  ──► P1_DIVERT      (box_class := LARGE)
                                  not tall ──► RUNNING      (box_class := SMALL or REJECT)
                                  no reading ──► box_class := REJECT, RUNNING
P1_DIVERT  : P1_VALVE = 1 ; exit on (P1_EXT ∧ ¬S1_BOX) or T_extend → FAULT
P1_RETURN  : P1_VALVE = 0 ; exit on P1_HOME or T_return → FAULT ; count_LARGE++
RUNNING ──(S2_BOX ↑ ∧ box_class = REJECT)──► P2_DIVERT
P2_DIVERT  : P2_VALVE = 1 ; exit on (P2_EXT ∧ S2_CLEAR) or T_extend → FAULT
P2_RETURN  : P2_VALVE = 0 ; exit on P2_HOME or T_return → FAULT ; count_REJECT++
RUNNING ──(S2_BOX ↑ ∧ box_class ≠ REJECT)──► RUNNING ; count_SMALL++ on S2_BOX falling edge
any state ──(STOP)──► STOPPING : outputs off ──► IDLE
any state ──(¬E_STOP_OK)──► ESTOP
any fault condition ──► FAULT
```

`RUNNING` exit order: S1 first, S2 second. Because station 2 is downstream, a box can only be at S2 after it has passed S1, so `box_class` is always valid.

## 5. State table

| # | State | MOTOR | P1 | P2 | Exit | Fault traps |
|---|-------|-------|----|----|------|-------------|
| 0 | `IDLE` | 0 | 0 | 0 | START + all healthy | — |
| 1 | `READY` | 0 | 0 | 0 | run seal set | — |
| 2 | `RUNNING` | 1 | 0 | 0 | S1_BOX↑, S2_BOX↑, STOP, fault | — |
| 3 | `S1_CLASSIFY` | 1 | 0 | 0 | 200 ms settle → tall / not tall | classify timeout → class := REJECT |
| 4 | `P1_DIVERT` | 1 | 1 | 0 | P1_EXT ∧ ¬S1_BOX | T_extend |
| 5 | `P1_RETURN` | 1 | 0 | 0 | P1_HOME | T_return |
| 6 | `P2_DIVERT` | 1 | 0 | 1 | P2_EXT ∧ S2_CLEAR | T_extend |
| 7 | `P2_RETURN` | 1 | 0 | 0 | P2_HOME | T_return |
| 8 | `STOPPING` | 0 | 0 | 0 | always → IDLE | — |
| 9 | `FAULT` | 0 | 0 | 0 | RESET after cause cleared | — |
| 10 | `ESTOP` | 0 (hardware) | 0 | 0 | RESET **and** E_STOP_OK | — |

Rule: **`P1_VALVE` may be 1 only in `P1_DIVERT`; `P2_VALVE` only in `P2_DIVERT`.** Any other combination is a defect.

`ESTOP` is entered from every state. The motor is already de-energized by hardware; the state exists so the sequence cannot resume by itself.

## 6. Ladder structure

| Group | Content |
|-------|---------|
| G1 Safety & enable | E_STOP_OK normalisation, MOTOR_OL, run seal, `reset_required` latch |
| G2 State machine | one rung per state (one-hot bits), transition guards, `box_class` memory |
| G3 Outputs | state → MOTOR_RUN / P1_VALVE / P2_VALVE / lamps (one coil per rung) |
| G4 Timers & faults | T_classify, T_extend(×2), T_return(×2), T_reject_lamp, fault latch + cause word |
| G5 Counters | count_SMALL, count_LARGE, count_REJECT (DINT) |

Rules applied everywhere:
- Seal-in for RUN and FAULT, never a bare momentary.
- Interlock first in the rung, action last — reviewer reads cause before effect.
- One coil per tag in the whole program.
- Timers reset by their own state exit, not by first-scan.

## 7. Classifier (single place to change)

```
tall := S1_SIZE                      (* 1 = box taller than 60 mm = LARGE *)
IF tall THEN box_class := LARGE
ELSIF NOT S1_BOX AND tall_ever_seen THEN ... (not needed in v1)
ELSE box_class := SMALL
```
On timeout / contradiction: `box_class := REJECT` (never guess). Swapping to colour touches this block only.

## 8. Timing constants (simulation values — confirm at bench)

| Name | Value | Reason |
|------|-------|--------|
| `T_classify` | 200 ms | sensor settle |
| `T_extend` | 800 ms | cylinder stroke guard |
| `T_return` | 1000 ms | spring-return guard |
| `T_reject_lamp` | 1000 ms | amber pulse |
| `T_debounce` | 20 ms | photo-eye chatter |
| box pitch (sim) | 400 mm | plant model |
| belt speed (sim) | 0.3 m/s | ⇒ ~45 boxes/min theoretical |

## 9. HMI — FUXA over Modbus TCP

OpenPLC Runtime v4 exposes the `modbus_slave` plugin (TCP, port 502). FUXA connects as Modbus TCP client.

| FUXA element | Modbus | OpenPLC tag |
|--------------|--------|-------------|
| Motor lamp | coil 0 | `MOTOR_RUN` |
| P1 coil / P2 coil | coils 1, 2 | `P1_VALVE`, `P2_VALVE` |
| Stack light trio | coils 3–5 | `LAMP_RUN/FAULT/REJECT` |
| Start / Stop | coils 80–81 (write) | `HMI_START` `%MX2.0`, `HMI_STOP` `%MX2.1` (never Reset — SR-08) |
| State number | holding register 0 | state integer |
| Counters | holding registers 1–3 | `count_SMALL/LARGE/REJECT` |
| Fault cause word | holding register 4 | `fault_cause` |

Keep a physical button path too — FUXA is an indicator/extra control, not the only way to stop the line. A web HMI is not a safety device.

## 10. Diagrams to produce

- `docs/img/layout.png` — 3 lanes, 2 stations
- `docs/img/state-machine.png` — from §4
- `docs/img/wiring.png` — power, contactor, E-Stop, 24 V, PLC I/O
- `docs/img/ladder-g1..g5.png` — printouts with rung comments
- `docs/img/fuxa.png` — HMI screen