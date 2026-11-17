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

Two clips, both from the live program (never a replay):

- `docs/demo.mp4` — the animated 3D run, with sound (58 s, 1920×1080, 10 fps, h264 + AAC, ~10 MB).
- `docs/hmi.mp4` — the earlier HMI-only clip, kept for comparison.
- `docs/img/viz-3d.png` — a still from the 3D video.

### `docs/demo.mp4` — how it was made

`sim/demo3d.py` runs three things at once against the live PLC:

1. `viz/` (Three.js, vendored in `viz/vendor/`, no CDN) is opened in a headless
   Chrome and records its own canvas with `MediaRecorder`, ten frames a second
   pushed on a timer, so playback is real time.
2. A second headless Chrome shows the real FUXA screen and is screenshotted
   twice a second.
3. The plant simulator and the PLC run the scenario; every actuator edge becomes
   a sound cue, and every phase change becomes a caption.

ffmpeg then lays the FUXA shots over the picture-in-picture area, draws the
captions, and muxes `audio/demo.wav`.

**What is simulator truth and what is the PLC's own.** Box positions, their
sizes, the "uncertain" flag and the chosen lane come from the plant simulator —
there are no physical sensors, so the view cannot invent a box. The motor, both
valve outputs, the state word, the three counters and the fault code come from
the PLC's coils and holding registers over Modbus, polled at about 16 Hz. If the
animation shows a pusher stroking or a counter climbing, the controller did it.

| Scene | Time | What it shows |
|-------|------|---------------|
| Title | 0:00-0:02 | machine still, belt running at 300 mm/s, no boxes |
| 10-box cycle | 0:04-0:31 | tall boxes pushed left to B, small carry straight out to A, boxes with an untrustworthy height reading rejected to C; first carton lands at 0:10, last at 0:30 |
| Done | 0:31-0:33 | counters read 4 SMALL / 3 LARGE / 3 REJECT |
| E-Stop mid-divert | 0:33-0:38 | pusher 1 energized, then the E-Stop clunk at 0:34.6: motor and both valves dead in the same scan, the box in the pusher zone stops where it is, counters frozen |
| Recovery 1 of 2 | 0:38-0:41 | Reset clears the fault and drops to IDLE; the line stays stopped and the counters are kept |
| Recovery 2 of 2 | 0:41-0:44 | a separate Start returns to RUNNING; the box that was in front of pusher 1 has no record any more, so station 2 sends it to REJECT — the design rule at work, not a demo trick |
| Line alive again | 0:44-0:50 | a fresh box goes straight through; counters kept across the fault |
| HMI Stop / HMI Start | 0:51-0:55 | real mousedown/mouseup on the FUXA buttons in the picture-in-picture |
| End | 0:55-0:58 | state and counters on the HMI, safety chain armed |

### Audio evidence (earshot)

Built by `audio/build.py` (motor bed, puffs, thuds and the E-Stop clunk are
numpy synthesis; the panel tap is rendered from the earshot bank). Verified with
the earshot tools:

| Check | Command | Result |
|-------|---------|--------|
| Loudness and peaks | `earshot measure audio/demo.wav` | −16.0 LUFS integrated, true peak −1.2 dBTP, LRA 15.9 LU, noise floor −33.3 dBFS |
| Delivered file | `earshot timeline docs/demo.mp4` | −16.0 LUFS, LRA 15.9 LU (codec overshoot at −0.4 dBTP on the decoded lossy copy) |
| Sync of cues to sound | `earshot timeline docs/demo.mp4 --cues cues.json` | 24/24 cues have an onset within one frame (chance 70%), median offset 0 ms, 100% land on an accent ≥ 6 dB over the bed (chance 10%) |
| No silent holes | same run | after the E-Stop the level reads −29 LUFS (room tone), not silence |
| Is it code-synthesised? | `earshot realness audio/demo.wav` | p_real 0.55 — "uncertain", 8 of 15 windows lean synthesised. The lowest window (0:00-0:04, p_real 0.16) is the pure numpy motor bed; the highest (0:40-0:44, p_real 0.94) is the same bed with the earshot bank tap on top |

Honest reading of that last row: earshot hears a mostly synthesised soundtrack,
which is exactly what it is. Stated in the README and the report; the synthetic
bed is credited as synthetic.

### Visual checks

Frames at 3, 12, 20, 28, 34, 40, 48 and 56 s were inspected: the legend, the
lane colours, the `?` marker on uncertain boxes, the photo-eye beams lighting
when blocked, the pusher stroke, the picture-in-picture HMI and every caption
render as intended, and the counters on the HMI agree with the words read back
by the test suite.
