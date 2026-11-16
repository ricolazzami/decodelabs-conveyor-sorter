# REQUIREMENTS — Project 4, PLC-Based Conveyor Sorting System

Source of truth: `Robotics and automation project 4_compressed.pdf` (DecodeLabs, Batch 2026).
Every item traces to a PDF line or is marked **[DERIVED]**. Nothing is silently assumed.

PDF quotes:

- G1: "Design the industrial control logic to sort boxes by size or color on an automated conveyor line."
- R1: "Map digital Inputs (sensors) and Outputs (motors, pneumatic pushers)."
- R2: "Write industrial Ladder Logic or a State Machine to control the sequence of operations."
- R3: "Implement strict safety interlocks (e.g., E-Stop logic halts all motors immediately)."
- S1: "PLC Programming basics, Ladder Logic, Sensor Integration, Industrial Safety Standards."

---

## 1. Functional requirements

| ID | Requirement | Traces to |
|----|-------------|-----------|
| FR-01 | The system shall classify each box by **size** (tall ≥ 100 mm vs short). | G1 |
| FR-02 | The system shall sort into **3 lanes**: SMALL (straight), LARGE (pusher 1), REJECT (pusher 2). | G1 |
| FR-03 | The system shall detect box presence at station 1 and station 2, with debounce, and latch the detection. | R1, S1 |
| FR-04 | The system shall drive the conveyor motor from one digital output (contactor / drive enable). | R1 |
| FR-05 | The system shall actuate each pneumatic pusher (solenoid valve) only inside its own divert state, and retract it before the next box arrives. | R1 |
| FR-06 | The system shall provide Start, Stop, and Reset from panel pushbuttons. | R2 [DERIVED] |
| FR-07 | The system shall run a fixed sequence: IDLE → READY → RUNNING → classify → divert → return, plus FAULT and ESTOP. | R2 |
| FR-08 | An unclassified box (sensor timeout or contradiction) shall be sent to REJECT, never guessed into SMALL or LARGE. | R3, S1 [DERIVED] |
| FR-09 | The system shall indicate RUN, FAULT and REJECT on a stack light. | R1 [DERIVED] |
| FR-10 | The system shall count boxes per lane (SMALL / LARGE / REJECT) and expose the counts to the HMI. | [DERIVED: verifiable acceptance] |
| FR-11 | The system shall expose state, counts, fault cause and a Start/Stop/Reset path on a web HMI (FUXA, Modbus TCP). | [DERIVED: chosen by user, not required by PDF] |
| FR-12 | Reset shall clear FAULT/ESTOP latch only; a separate Start press shall be required to run again. | R3 [DERIVED] |

## 2. I/O requirements

| ID | Requirement | Traces to |
|----|-------------|-----------|
| IO-01 | Every sensor, pushbutton, and valve shall appear in one I/O map with tag, address, signal type, and safe state. | R1 |
| IO-02 | Safety inputs (E-Stop) shall be normally closed and wired fail-safe. | R3 |
| IO-03 | Every output shall be de-energized = safe (motor off, both valves retracted). | R3 |
| IO-04 | Spare I/O shall be listed with its reserved purpose. | R1 |
| IO-05 | The program shall need no more than 16 DI and 8 DO (training-kit class). | [DERIVED] |

## 3. Safety requirements (the graded part)

| ID | Requirement | Traces to |
|----|-------------|-----------|
| SR-01 | E-Stop shall halt all motors immediately, independent of PLC scan. | R3 |
| SR-02 | E-Stop shall remove motor power and de-energize **both** pusher valves. | R3 |
| SR-03 | E-Stop shall be hardwired in the contactor coil circuit; PLC logic alone is not acceptable. | R3, S1 |
| SR-04 | Recovery shall require E-Stop released **and** a Reset press; no automatic restart. | R3 [DERIVED] |
| SR-05 | A fault (sensor contradiction, pusher not home, motor overload) shall stop the sequence and require Reset. | R3 |
| SR-06 | A pusher shall not extend outside its divert state or while no box is in its zone. | R3 [DERIVED] |
| SR-07 | Each pusher shall have an extend and a return timeout; on timeout → retract command + FAULT. | R3 [DERIVED] |
| SR-08 | The HMI shall not be able to clear a safety latch without the physical Reset input. | R3 [DERIVED: web HMI is not a safety device] |

## 4. Non-functional requirements

| ID | Requirement | Traces to |
|----|-------------|-----------|
| NFR-01 | Logic shall be deterministic: one scan-cycle sequence, no race between divert and detection. | R2 |
| NFR-02 | Program shall be documented: rung comments, tag descriptions, state diagram. | S1 |
| NFR-03 | Logic shall be portable IEC 61131-3 ladder (contacts, coils, TON, CTU/DINT) across OpenPLC and Siemens LAD. | R2 |
| NFR-04 | Sorting accuracy shall be 100% on the defined test set; 0 mis-sorts. | G1 |
| NFR-05 | Throughput target: ~45 boxes/min **as a simulation assumption** (400 mm pitch, 0.3 m/s belt), labelled as an assumption, not a measurement. | G1 |
| NFR-06 | Cold start shall reach IDLE without forcing tags. | R2 |
| NFR-07 | Every test case shall be reproducible from a scripted plant simulator, not only by hand. | [DERIVED] |

## 5. Out of scope

- Mechanical fabrication, conveyor frame, pneumatic sizing
- Vision / AI classification
- SCADA/MES integration beyond the FUXA HMI
- Accumulation, merge, recirculation
- Formal safety certification (ISO 13849 PL / IEC 62061 SIL assessment)
- Factory I/O as a required dependency (Windows-only, optional visual)

## 6. Acceptance criteria

1. I/O map matches program tags 1:1 (IO-01)
2. E-Stop stops the motor and retracts **both** pushers from every state (SR-01, SR-02)
3. Releasing E-Stop does not restart; Reset then Start does (SR-04, FR-12)
4. 10 mixed boxes (4 small, 3 large, 3 reject-forced) land in the correct lanes, 0 mis-sorts (NFR-04)
5. All cases in `docs/TEST-PLAN.md` pass with evidence (video, screenshot, simulator log)
6. Ladder source, commented printout, I/O table, state diagram and HMI screenshots are submitted
7. Report states plainly which safety claims are hardware-verified and which are design-only