(*
  Project 4 - PLC-Based Conveyor Sorting System
  DecodeLabs Robotics & Automation, Batch 2026

  Sort by size into 3 lanes:
    lane A (straight) = SMALL   - no actuator, fail-safe default
    lane B (pusher 1) = LARGE   - diverted at station 1
    lane C (pusher 2) = REJECT  - uncertain size reading, or a box with no record

  Two layers, on purpose:

    A) CLASSIFICATION, run on every scan, never blocked by an actuator.
       A box at station 1 is sampled twice, 200 ms apart. That is the only time
       the box is under the sensor, so the sampling must not wait for the
       sequencer to be free - otherwise the reading is taken after the box has
       gone and a good box looks unclassified.

    B) ACTUATOR SEQUENCE (the state machine).
       LARGE boxes are counted in p1_queued and diverted at station 1.
       Everything else goes into a small FIFO in arrival order and is decided at
       station 2: SMALL straight, REJECT through pusher 2. Diverted LARGE boxes
       never reach station 2, so they are deliberately kept out of that FIFO and
       the order of the FIFO always matches the order of boxes on the belt.

  Safety:
    E-Stop is hardwired in the motor contactor circuit (see docs/SAFETY.md).
    This program only does the bookkeeping: it forces the sequence into ESTOP,
    retracts both cylinders, and latches reset_required.
    Recovery needs E-Stop released AND a physical Reset, then Start.

  Language: Structured Text state machine (IEC 61131-3), brief requirement R2.
  State / counter words are in %QW so the FUXA HMI can read them.
*)
PROGRAM main
VAR
  (* ---------- digital inputs (delivered over Modbus by the plant) ---------- *)
  E_STOP_OK  : BOOL AT %MX0.0;   (* NC: 1 = healthy, 0 = pressed  *)
  PB_START   : BOOL AT %MX0.1;   (* NO                            *)
  PB_STOP    : BOOL AT %MX0.2;   (* NC: 1 = not pressed           *)
  PB_RESET   : BOOL AT %MX0.3;   (* NO                            *)
  S1_BOX     : BOOL AT %MX0.4;   (* photo-eye, station 1          *)
  S1_SIZE    : BOOL AT %MX0.5;   (* height photo-eye at 60 mm     *)
  S2_BOX     : BOOL AT %MX0.6;   (* photo-eye, station 2          *)
  P1_HOME    : BOOL AT %MX0.7;   (* cylinder 1 retracted          *)
  P1_EXT     : BOOL AT %MX1.0;   (* cylinder 1 extended           *)
  P2_HOME    : BOOL AT %MX1.1;   (* cylinder 2 retracted          *)
  P2_EXT     : BOOL AT %MX1.2;   (* cylinder 2 extended           *)
  MOTOR_OL   : BOOL AT %MX1.3;   (* NC: 1 = healthy               *)
  MODE_AUTO  : BOOL AT %MX1.4;   (* selector, forced 1 in sim     *)
  S2_CLEAR   : BOOL AT %MX1.5;   (* station 2 exit                *)

  (* ---------- digital outputs ---------- *)
  MOTOR_RUN  : BOOL AT %QX0.0;
  P1_VALVE   : BOOL AT %QX0.1;
  P2_VALVE   : BOOL AT %QX0.2;
  LAMP_RUN   : BOOL AT %QX0.3;
  LAMP_FAULT : BOOL AT %QX0.4;
  LAMP_REJ   : BOOL AT %QX0.5;

  (* ---------- HMI memory coils (Modbus %MX2.x = coils 80/81) ---------- *)
  HMI_START  : BOOL AT %MX2.0;
  HMI_STOP   : BOOL AT %MX2.1;

  (* ---------- HMI words (Modbus holding registers) ---------- *)
  ST_WORD    : INT AT %QW0;      (* current state number          *)
  CNT_SMALL  : INT AT %QW1;
  CNT_LARGE  : INT AT %QW2;
  CNT_REJECT : INT AT %QW3;
  FAULT_CODE : INT AT %QW4;

  (* ---------- classification ---------- *)
  size_sample  : BOOL := FALSE;  (* S1_SIZE latched at the rising edge *)
  s1_pending   : BOOL := FALSE;  (* a box is under sensor 1, not yet classified *)
  s2_pending   : BOOL := FALSE;  (* a box reached station 2, not yet decided *)
  p1_queued    : INT  := 0;      (* LARGE boxes waiting for pusher 1 *)
  q_class      : ARRAY[0..7] OF INT;   (* 0 SMALL, 2 REJECT; never 1 *)
  q_head       : INT := 0;
  q_tail       : INT := 0;

  (* ---------- actuator sequence ---------- *)
  state        : INT := 0;
  reset_required : BOOL := FALSE;
  p1_did_ext   : BOOL := FALSE;
  p2_did_ext   : BOOL := FALSE;
  drop_class   : INT := 0;       (* class popped for the box at station 2 *)

  (* ---------- input conditioning ---------- *)
  stop_cmd     : BOOL;
  safety_ok    : BOOL;
  start_act    : BOOL;
  reset_act    : BOOL;
  start_prev   : BOOL := FALSE;
  stop_prev    : BOOL := TRUE;
  reset_prev   : BOOL := FALSE;
  s1_prev      : BOOL := FALSE;
  s2_prev      : BOOL := FALSE;
  s1_rise      : BOOL;
  s2_rise      : BOOL;
  start_rise   : BOOL;
  reset_rise   : BOOL;
  stop_rise    : BOOL;

  t_settle     : TON;
  t_p1_ext     : TON;
  t_p1_ret     : TON;
  t_p2_ext     : TON;
  t_p2_ret     : TON;
  t_s2_stuck   : TON;
  t_lamp       : TON;
END_VAR

(* ============================================================
   1. Input conditioning and edges
   ============================================================ *)
stop_cmd  := NOT PB_STOP;
safety_ok := E_STOP_OK AND MOTOR_OL;
start_act := PB_START OR HMI_START;
reset_act := PB_RESET;

start_rise := start_act AND NOT start_prev;
stop_rise  := stop_cmd AND NOT stop_prev;
reset_rise := reset_act AND NOT reset_prev;
s1_rise    := S1_BOX AND NOT s1_prev;
s2_rise    := S2_BOX AND NOT s2_prev;

(* an arrival is latched: the sequencer may be busy with the other station *)
IF s2_rise THEN s2_pending := TRUE; END_IF;

(* ============================================================
   2. CLASSIFICATION - runs every scan, independent of the sequence
   ============================================================ *)
(* a new box under sensor 1: latch the reading now, because now is the
   only moment the box is actually there *)
IF s1_rise THEN
  size_sample := S1_SIZE;
  s1_pending  := TRUE;
END_IF;

(* the timer must be CALLED every scan - an IEC timer only updates its Q when
   the function block is executed, not by the passage of time alone *)
t_settle(IN := s1_pending AND S1_BOX, PT := T#200ms);

IF s1_pending AND t_settle.Q THEN
  (* second sample, 200 ms after the first *)
  IF S1_SIZE <> size_sample THEN
    drop_class := 2;             (* the two readings disagree -> not trusted *)
  ELSIF size_sample THEN
    drop_class := 1;             (* LARGE *)
  ELSE
    drop_class := 0;             (* SMALL *)
  END_IF;
  s1_pending := FALSE;

  IF drop_class = 1 THEN
    p1_queued := p1_queued + 1;  (* diverted at station 1; never reaches st.2 *)
  ELSE
    q_class[q_tail MOD 8] := drop_class;
    q_tail := q_tail + 1;
  END_IF;
END_IF;

(* the box left before a stable reading: it must not silently become SMALL *)
IF s1_pending AND NOT S1_BOX THEN
  q_class[q_tail MOD 8] := 2;
  q_tail := q_tail + 1;
  s1_pending := FALSE;
END_IF;

(* ============================================================
   3. Supervisory faults - these win over the sequence
   ============================================================ *)
IF NOT E_STOP_OK THEN
  state := 10;                       (* ESTOP *)
  reset_required := TRUE;
  FAULT_CODE := 1;
ELSIF NOT MOTOR_OL THEN
  state := 9;                        (* FAULT *)
  reset_required := TRUE;
  FAULT_CODE := 6;
END_IF;

(* ============================================================
   4. Actuator sequence
   ============================================================ *)
CASE state OF
  0: (* IDLE *)
    IF start_rise AND safety_ok AND P1_HOME AND P2_HOME
       AND NOT reset_required AND MODE_AUTO THEN
      state := 1;
    END_IF;

  1: (* READY -> RUNNING : clear the tracking, never the counters *)
    q_head := 0;
    q_tail := 0;
    p1_queued := 0;
    s1_pending := FALSE;
    s2_pending := FALSE;
    state := 2;

  2: (* RUNNING *)
    IF stop_rise OR NOT safety_ok THEN
      state := 8;
    ELSIF s2_pending THEN
      (* station 2 first: that box is further down the belt than any box still
         waiting for pusher 1, and its record was taken at station 1 *)
      s2_pending := FALSE;
      IF q_head < q_tail THEN
        drop_class := q_class[q_head MOD 8];
        q_head := q_head + 1;
      ELSE
        drop_class := 2;             (* no record -> never guess *)
      END_IF;
      IF drop_class = 0 THEN
        CNT_SMALL := CNT_SMALL + 1;
      ELSE
        p2_did_ext := FALSE;
        state := 6;                  (* P2_DIVERT: reject lane *)
      END_IF;
    ELSIF p1_queued > 0 AND P1_HOME THEN
      p1_did_ext := FALSE;
      state := 4;                    (* P1_DIVERT, at station 1 *)
    END_IF;

  3: (* reserved: classification is the always-scanned block above *)
    state := 2;

  4: (* P1_DIVERT - leave only after the cylinder is really out *)
    IF P1_EXT THEN
      p1_did_ext := TRUE;
      t_p1_ext(IN := FALSE, PT := T#800ms);
      state := 5;
    ELSIF t_p1_ext.Q THEN
      FAULT_CODE := 2;
      state := 9;
    ELSE
      t_p1_ext(IN := TRUE, PT := T#800ms);
    END_IF;

  5: (* P1_RETURN - only a real extension needs a return *)
    IF P1_HOME OR NOT p1_did_ext THEN
      t_p1_ret(IN := FALSE, PT := T#1000ms);
      IF p1_did_ext AND p1_queued > 0 THEN
        p1_queued := p1_queued - 1;
      END_IF;
      CNT_LARGE := CNT_LARGE + 1;
      state := 2;
    ELSIF t_p1_ret.Q THEN
      FAULT_CODE := 3;
      state := 9;
    ELSE
      t_p1_ret(IN := TRUE, PT := T#1000ms);
    END_IF;

  6: (* P2_DIVERT - leave only after the cylinder is really out *)
    IF P2_EXT THEN
      p2_did_ext := TRUE;
      t_p2_ext(IN := FALSE, PT := T#800ms);
      state := 7;
    ELSIF t_p2_ext.Q THEN
      FAULT_CODE := 4;
      state := 9;
    ELSE
      t_p2_ext(IN := TRUE, PT := T#800ms);
    END_IF;

  7: (* P2_RETURN *)
    IF P2_HOME OR NOT p2_did_ext THEN
      t_p2_ret(IN := FALSE, PT := T#1000ms);
      CNT_REJECT := CNT_REJECT + 1;
      LAMP_REJ := TRUE;
      t_lamp(IN := TRUE, PT := T#1000ms);
      state := 2;
    ELSIF t_p2_ret.Q THEN
      FAULT_CODE := 5;
      state := 9;
    ELSE
      t_p2_ret(IN := TRUE, PT := T#1000ms);
    END_IF;

  8: (* STOPPING *)
    q_head := 0;
    q_tail := 0;
    p1_queued := 0;
    s1_pending := FALSE;
    s2_pending := FALSE;
    state := 0;

  9: (* FAULT - stays until Reset *)
    IF stop_rise THEN
      state := 8;
    END_IF;

  10: (* ESTOP - hardware already removed motor power *)
    IF stop_rise AND E_STOP_OK THEN
      state := 0;
    END_IF;
END_CASE;

(* ============================================================
   5. Reset
   ============================================================ *)
IF reset_rise AND E_STOP_OK AND MOTOR_OL THEN
  reset_required := FALSE;
  FAULT_CODE := 0;
  IF state = 9 OR state = 10 THEN
    state := 0;
  END_IF;
END_IF;

(* ============================================================
   6. Process monitors
   ============================================================ *)
IF S1_BOX AND S2_BOX THEN
  t_s2_stuck(IN := TRUE, PT := T#3000ms);
  IF t_s2_stuck.Q THEN
    FAULT_CODE := 7;
    state := 9;
  END_IF;
ELSE
  t_s2_stuck(IN := FALSE, PT := T#3000ms);
END_IF;

IF NOT t_lamp.Q THEN
  LAMP_REJ := (state = 7);
END_IF;

(* ============================================================
   7. Output mapping - generated from the state, never ad hoc
   ============================================================ *)
MOTOR_RUN  := (state = 2) OR (state = 4) OR (state = 5)
              OR (state = 6) OR (state = 7);
P1_VALVE   := (state = 4);
P2_VALVE   := (state = 6);
LAMP_RUN   := MOTOR_RUN;
LAMP_FAULT := (state = 9) OR (state = 10);

(* hard guard: nothing may drive a cylinder outside its state,
   and nothing may run without a healthy safety chain          *)
IF NOT safety_ok THEN
  MOTOR_RUN := FALSE;
  P1_VALVE  := FALSE;
  P2_VALVE  := FALSE;
END_IF;
IF state <> 4 THEN P1_VALVE := FALSE; END_IF;
IF state <> 6 THEN P2_VALVE := FALSE; END_IF;

(* ============================================================
   8. HMI words and edge memory
   ============================================================ *)
ST_WORD := state;

start_prev := start_act;
stop_prev  := stop_cmd;
reset_prev := reset_act;
s1_prev    := S1_BOX;
s2_prev    := S2_BOX;
END_PROGRAM
