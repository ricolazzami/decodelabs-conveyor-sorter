"""Acceptance tests TC-01 ... TC-14 against the live PLC (see docs/TEST-PLAN.md).

Each case starts from a clean PLC state: a stop/start cycle reloads the uploaded
program, which is what clears the counters and the box tracking.

Run it with the runtime up:
    .venv/bin/python sim/tests.py
    .venv/bin/python sim/tests.py TC-10 TC-13

Results are printed and written to docs/TEST-LOG.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib3
from pathlib import Path

import requests
from pymodbus.client import ModbusTcpClient

from plant import MX_BITS, TICK_S, Box, Plant, Plc

urllib3.disable_warnings()          # the runtime's certificate is self-signed
ROOT = Path(__file__).resolve().parent.parent
RUNNING, FAULT, ESTOP = 2, 9, 10
HMI_START_COIL, HMI_STOP_COIL = 80, 81     # %MX2.0 / %MX2.1 in the program


# ---------------------------------------------------------------- infrastructure
def load_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Runtime:
    """The REST side of the runtime: only used to reload the program."""

    def __init__(self) -> None:
        env = load_env()
        self.base = "https://localhost:8443/api"
        self.token = requests.post(f"{self.base}/login", verify=False, timeout=10,
                                   json={"username": env["OPENPLC_USER"],
                                         "password": env["OPENPLC_PASSWORD"]}
                                   ).json()["access_token"]

    def api(self, path: str) -> str:
        r = requests.get(f"{self.base}/{path}", verify=False, timeout=30,
                         headers={"Authorization": f"Bearer {self.token}"})
        return r.json().get("status", "")

    def wait(self, want: str, timeout_s: float = 30) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            if want in self.api("status"):
                return True
            time.sleep(0.25)
        return False

    def cycle(self) -> None:
        """Reload the program: counters and tracking return to their start value."""
        for _ in range(3):
            self.api("stop-plc")
            if self.wait("STATUS:STOPPED"):
                break
        for _ in range(3):
            self.api("start-plc")
            if self.wait("STATUS:RUNNING"):
                return
        raise SystemExit("could not restart the PLC")


class Plc2(Plc):
    """Plc plus the small conveniences the tests need."""

    def healthy(self) -> None:
        self.write_inputs({"E_STOP_OK": True, "PB_START": False, "PB_STOP": True,
                           "PB_RESET": False, "S1_BOX": False, "S1_SIZE": False,
                           "S2_BOX": False, "P1_HOME": True, "P1_EXT": False,
                           "P2_HOME": True, "P2_EXT": False, "MOTOR_OL": True,
                           "MODE_AUTO": True, "S2_CLEAR": False})

    def w(self, **kw: bool) -> None:
        self.write_inputs({k: v for k, v in kw.items()})

    def words(self) -> dict[str, int]:
        return self.read_words()

    def qx(self) -> dict[str, bool]:
        return self.read_outputs()

    def state(self) -> int:
        return self.read_words()["state"]

    def boot(self) -> None:
        """Healthy field image, latch cleared, standing in IDLE."""
        self.healthy()
        time.sleep(0.15)
        self.pulse("PB_RESET")
        time.sleep(0.15)

    def start(self) -> bool:
        self.pulse("PB_START")
        time.sleep(0.3)
        return self.state() == RUNNING

    def feed(self, boxes: list[Box], until_s: float = 40.0,
             pitch: float = 400.0) -> dict:
        """Run the plant until every box has landed, watching the invariants.

        Then keep the clock running until the PLC has finished what it started:
        a box has a lane the moment a pusher moves it, but the counter is written
        when the cylinder is home again. Stopping at "lane assigned" would read
        the counters one actuator cycle too early.
        """
        plant = Plant(boxes=boxes, pitch_mm=pitch, start_delay_s=0.5)
        seen_states: list[int] = []
        violations: list[str] = []
        lamp_seen = False
        t0 = time.time()
        while plant.t < until_s and not plant.done():
            self._tick(plant, seen_states, violations, lamp_seen_point=True)
            lamp_seen = lamp_seen or self.read_outputs()["LAMP_REJ"]
        while plant.t < until_s + 5.0:
            qx = self.read_outputs()
            st = self.read_words()["state"]
            lamp_seen = lamp_seen or qx["LAMP_REJ"]
            if not seen_states or seen_states[-1] != st:
                seen_states.append(st)
            if st == RUNNING and not (qx["P1_VALVE"] or qx["P2_VALVE"]):
                break
            plant.step(TICK_S, qx)
            self.write_inputs(plant.inputs())
            time.sleep(TICK_S)
        return {"boxes": boxes, "events": plant.events, "states": seen_states,
                "violations": violations, "lamp": lamp_seen,
                "wall_s": round(time.time() - t0, 1), "words": self.read_words()}

    def _tick(self, plant: Plant, seen_states: list[int], violations: list[str],
              lamp_seen_point: bool = False) -> None:
        qx = self.read_outputs()
        st = self.read_words()["state"]
        if qx["P1_VALVE"] and st != 4:
            violations.append(f"P1_VALVE in state {st}")
        if qx["P2_VALVE"] and st != 6:
            violations.append(f"P2_VALVE in state {st}")
        if qx["P1_VALVE"] and qx["P2_VALVE"]:
            violations.append("both valves at once")
        if not seen_states or seen_states[-1] != st:
            seen_states.append(st)
        plant.step(TICK_S, qx)
        self.write_inputs(plant.inputs())
        time.sleep(TICK_S)

    def service(self, plant: Plant, seconds: float, **overrides: bool) -> None:
        """Let an empty plant answer the PLC: cylinder reeds, no boxes.

        Used by the cases that poke a single input and then need the sequence to
        be able to finish. Without cylinder feedback the state machine would sit
        in P1_DIVERT / P2_DIVERT forever, which is correct behaviour but useless
        as a test of counting.
        """
        end = time.time() + seconds
        while time.time() < end:
            self.write_inputs({**plant.inputs(), **overrides})
            plant.step(TICK_S, self.read_outputs())
            time.sleep(TICK_S)
        self.write_inputs({**plant.inputs(), **overrides})

    def pump(self, plant: Plant, seconds: float, overrides: dict | None = None,
             watch=None) -> list[int]:
        """Run the plant clock, forcing `overrides` on top of the plant image.

        Returns the list of distinct PLC states seen. `watch(qx, st)` is called
        once per tick, which is how a case proves something never happened.
        """
        states: list[int] = []
        end = time.time() + seconds
        while time.time() < end:
            qx = self.read_outputs()
            st = self.read_words()["state"]
            if not states or states[-1] != st:
                states.append(st)
            if watch:
                watch(qx, st)
            plant.step(TICK_S, qx)
            self.write_inputs({**plant.inputs(), **(overrides or {})})
            time.sleep(TICK_S)
        return states

    def coil(self, addr: int, value: bool) -> None:
        """Write a raw Modbus coil - used to play at being the HMI."""
        self._retry(lambda: self.c.write_coil(addr, bool(value), device_id=self.device_id))

    def pulse_ms(self, name: str, ms: int) -> None:
        self.w(**{name: True})
        time.sleep(ms / 1000.0)
        self.w(**{name: False})



class Hmi:
    """The FUXA web screen, read the way the browser reads it (socket.io).

    `snapshot()` returns the values the HMI server is serving, keyed by the tag
    name without the device prefix. Used by TC-27...TC-30.
    """

    TAGS = ["ST_WORD", "CNT_SMALL", "CNT_LARGE", "CNT_REJECT", "FAULT_CODE",
            "MOTOR_RUN", "P1_VALVE", "P2_VALVE", "LAMP_RUN", "LAMP_FAULT", "LAMP_REJ"]

    def __init__(self, url: str = "http://localhost:1881", device: str = "PLC"):
        import socketio
        self.socketio = socketio
        self.url, self.device = url, device

    def snapshot(self, timeout_s: float = 20.0) -> dict[str, int]:
        """One *live* frame. The first frame is the server's cached one, so the
        test waits for the next poll instead of reading a value up to 1 s old."""
        sio = self.socketio.Client(reconnection=False)
        got: dict[str, int] = {}
        frames = [0]

        @sio.on("device-values")
        def values(msg):
            if len(msg.get("values", [])) < len(self.TAGS):
                return
            for v in msg["values"]:
                got[v["id"].split("^~^")[-1]] = v["value"]
            frames[0] += 1

        sio.connect(self.url, transports=["websocket", "polling"])
        try:
            sio.emit("device-tags-subscribe",
                     {"tagsId": [f"{self.device}^~^{t}" for t in self.TAGS],
                      "sendLastValue": True})
            end = time.time() + timeout_s
            while time.time() < end and frames[0] < 2:
                time.sleep(0.1)
        finally:
            sio.disconnect()
        return got if frames[0] >= 2 else {}

    def matches(self, plc) -> tuple[bool, str]:
        h = self.snapshot()
        w, q = plc.read_words(), plc.read_outputs()
        names = {"state": "ST_WORD", "count_small": "CNT_SMALL",
                 "count_large": "CNT_LARGE", "count_reject": "CNT_REJECT",
                 "fault_code": "FAULT_CODE"}
        want = {names.get(k, k): int(v) for k, v in w.items()}
        want.update({k: int(v) for k, v in q.items()
                     if k in ("MOTOR_RUN", "P1_VALVE", "P2_VALVE",
                              "LAMP_RUN", "LAMP_FAULT", "LAMP_REJ")})
        bad = {k: (v, h.get(k)) for k, v in want.items() if h.get(k) != v}
        return not bad, ("HMI matches the runtime" if not bad
                         else "; ".join(f"{k} PLC {v[0]} vs HMI {v[1]}" for k, v in bad.items()))

# ---------------------------------------------------------------- the cases
def tc01(ctx) -> tuple[bool, str]:
    """Tag list against the I/O map: 1:1, no orphan."""
    st = (ROOT / "src/conveyor-sorter/conveyor-sorter/pous/programs/main.st").read_text()
    declared = set(re.findall(r"(\w+)\s*:\s*\w+\s+AT\s+%(?:MX|QX|QW)\d", st))
    doc = (ROOT / "docs/ARCHITECTURE.md").read_text()
    io = set(MX_BITS) | {"MOTOR_RUN", "P1_VALVE", "P2_VALVE", "LAMP_RUN",
                         "LAMP_FAULT", "LAMP_REJ", "HMI_START", "HMI_STOP",
                         "ST_WORD", "CNT_SMALL", "CNT_LARGE", "CNT_REJECT", "FAULT_CODE"}
    missing_doc = sorted(t for t in declared if t not in doc)
    missing_io = sorted(io - declared)
    ok = not missing_doc and not missing_io
    return ok, (f"{len(declared)} located tags; undocumented: {missing_doc or 'none'}; "
                f"locatable from the I/O map but missing from the program: {missing_io or 'none'}")


def tc02(ctx) -> tuple[bool, str]:
    """Cold start. With the Modbus image at zero, `E_STOP_OK` reads 0, which
    means "pressed", so the safe state is ESTOP - that is the point of an NC
    contact. A healthy chain plus Reset must take it to IDLE with everything off.
    """
    plc = ctx["plc"]
    qx = plc.qx()
    cold_state = plc.state()
    cold_off = not (qx["MOTOR_RUN"] or qx["P1_VALVE"] or qx["P2_VALVE"])
    plc.boot()
    idle = plc.state()
    idle_off = not any(plc.qx().values())
    ok = (cold_state == ESTOP and cold_off and cold_off
          and idle == 0 and idle_off and plc.words()["fault_code"] == 0)
    return ok, (f"zeroed image -> state {cold_state} (ESTOP, motor+valves off={cold_off}); "
                f"healthy + Reset -> state {idle}, all outputs off={idle_off}, "
                f"fault {plc.words()['fault_code']}")


def tc03(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    started = plc.start()
    ok = started and plc.qx()["MOTOR_RUN"]
    return ok, f"state {plc.state()}, MOTOR_RUN={plc.qx()['MOTOR_RUN']}"


def tc04(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    plc.start()
    plc.w(PB_STOP=False)
    time.sleep(1.0)
    ok = plc.state() == 0 and not plc.qx()["MOTOR_RUN"]
    plc.w(PB_STOP=True)
    return ok, f"after Stop: state {plc.state()}, MOTOR_RUN={plc.qx()['MOTOR_RUN']}"


def tc05(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    plc.w(E_STOP_OK=False)          # E-Stop pressed
    time.sleep(0.2)
    plc.pulse("PB_START")
    time.sleep(0.3)
    ok = plc.state() == ESTOP and not plc.qx()["MOTOR_RUN"]
    return ok, f"state {plc.state()} (must stay ESTOP), MOTOR_RUN={plc.qx()['MOTOR_RUN']}"


def tc06(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    qx = plc.qx()
    ok = not (qx["MOTOR_RUN"] or qx["P1_VALVE"] or qx["P2_VALVE"])
    return ok, f"power-up with no command: {qx}"


def tc07(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="small")])
    b = r["boxes"][0]
    ok = b.lane == "small" and r["words"]["count_small"] == 1 and not r["violations"]
    return ok, f"lane={b.lane}, count_small={r['words']['count_small']}, valve violations={r['violations'] or 'none'}"


def tc08(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="large")])
    b = r["boxes"][0]
    ok = (b.lane == "large" and r["words"]["count_large"] == 1
          and 4 in r["states"] and not r["violations"])
    return ok, f"lane={b.lane}, count_large={r['words']['count_large']}, states={r['states']}"


def tc09(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="small", uncertain=True)])
    b = r["boxes"][0]
    ok = (b.lane == "reject" and r["words"]["count_reject"] == 1 and r["lamp"]
          and r["words"]["state"] == RUNNING and not r["violations"])
    return ok, (f"lane={b.lane}, count_reject={r['words']['count_reject']}, amber={r['lamp']}, "
                f"final state {r['words']['state']}")


def tc10(ctx) -> tuple[bool, str]:
    """FR-03: one box edge may produce at most one record.

    Two rounds, both against an empty plant so the sequence can finish:

    - a 10 ms presence pulse. The task is 20 ms, so this may be missed entirely -
      which is allowed. What is not allowed is two counts from one edge.
    - a clean box (the beam blocked for half a second, then clear), which must
      count exactly once.
    """
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[], start_delay_s=0.0)

    before = plc.words()["count_small"]
    plc.pulse_ms("S1_BOX", 10)          # too short for the scan - may be missed
    plc.service(plant, 0.4)
    plc.pulse_ms("S2_BOX", 60)          # the arrival the record belongs to
    plc.service(plant, 3.0)
    ghost = plc.words()["count_small"] - before

    before = plc.words()["count_small"]
    plc.service(plant, 0.5, S1_BOX=True, S1_SIZE=False)   # a real short box passes
    plc.pulse_ms("S2_BOX", 60)
    plc.service(plant, 3.0)
    clean = plc.words()["count_small"] - before

    ok = ghost in (0, 1) and clean == 1 and plc.state() == RUNNING
    return ok, (f"one 10 ms edge -> {ghost} count(s) (0 is allowed, 2 is not); "
                f"one clean box -> {clean} (must be 1); final state {plc.state()}")


def tc11(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="large")])
    st = r["states"]
    ok = (4 in st and 5 in st and st[-1] == RUNNING and not r["violations"])
    return ok, f"states={st}, count_large={r['words']['count_large']}"


def tc12(ctx) -> tuple[bool, str]:
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="small", uncertain=True)])
    st = r["states"]
    ok = 6 in st and 7 in st and st[-1] == RUNNING and not r["violations"]
    return ok, f"states={st}, count_reject={r['words']['count_reject']}"


def tc13(ctx) -> tuple[bool, str]:
    out = subprocess.run([sys.executable, "sim/plant.py", "--scenario",
                          "sim/scenarios/acceptance-10.json",
                          "--log", "sim/logs/tc-13.log"],
                         cwd=ROOT, capture_output=True, text=True)
    tail = (ROOT / "sim/logs/tc-13.log").read_text().strip().splitlines()[-1]
    return out.returncode == 0, f"{tail} (also covered by sim/run-scenarios.sh)"


def tc14(ctx) -> tuple[bool, str]:
    """Two boxes back to back: neither is lost, and the classes stay with their box."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    r = plc.feed([Box(id=1, size="large"), Box(id=2, size="small")], pitch=300.0)
    lanes = [b.lane for b in r["boxes"]]
    w = r["words"]
    ok = (lanes == ["large", "small"] and w["count_large"] == 1
          and w["count_small"] == 1 and not r["violations"])
    return ok, f"lanes={lanes}, counts={w['count_small']}/{w['count_large']}/{w['count_reject']}"


# ---------------------------------------------------------------- safety cases
def _sit_in(plc: Plc2, target: int, faults: dict | None = None,
            boxes: list[Box] | None = None, timeout: float = 30.0):
    """Bring the line up and run it until the PLC is in `target`.

    Returns the plant if the state was reached, otherwise None. Only the states
    a line actually passes through can be entered this way; STOPPING (8) lasts a
    single scan, and ESTOP is TC-15's job.
    """
    plc.boot()
    if not plc.start():
        return None
    plant = Plant(boxes=boxes or [Box(id=1, size="large"), Box(id=2, size="small"),
                                  Box(id=3, size="small", uncertain=True)],
                  start_delay_s=0.5, faults=faults or {})
    if target == 0:
        return plant                       # already sitting in IDLE
    end = time.time() + timeout
    while time.time() < end:
        qx = plc.read_outputs()
        if plc.read_words()["state"] == target:
            return plant
        plant.step(TICK_S, qx)
        plc.write_inputs(plant.inputs())
        time.sleep(TICK_S)
    return None


def tc15(ctx) -> tuple[bool, str]:
    """SR-01/SR-02: E-Stop mid-run. Motor drops, valves drop, counters frozen."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[Box(id=1, size="large"), Box(id=2, size="small")],
                  start_delay_s=0.5, faults={"estop_at_s": 3.0})
    plc.pump(plant, 2.5)                 # running normally
    before = plc.words()
    trip = plc.pump(plant, 2.0)          # the plant presses the mushroom at t=3 s
    after = plc.words()
    still = plc.pump(plant, 1.0)
    frozen = (after["count_small"], after["count_large"], after["count_reject"]) == \
             (before["count_small"], before["count_large"], before["count_reject"])
    ok = (ESTOP in trip and trip[-1] == ESTOP and still == [ESTOP] and after["fault_code"] == 1
          and not plc.qx()["MOTOR_RUN"] and not plc.qx()["P1_VALVE"]
          and not plc.qx()["P2_VALVE"] and plc.qx()["LAMP_FAULT"] and frozen)
    return ok, (f"states {trip} then {still}; fault {after['fault_code']}; motor and both valves "
                f"off; red lamp on; counters frozen at "
                f"{after['count_small']}/{after['count_large']}/{after['count_reject']}")


def tc16(ctx) -> tuple[bool, str]:
    """SR-01: the E-Stop lands the same way from whichever state it is pressed in."""
    plc, rt = ctx["plc"], ctx["rt"]
    checked, bad = [], []
    for target in (0, RUNNING, 4, 5, 6, 7, FAULT):
        rt.cycle()
        plc.wait_ready()
        plant = _sit_in(plc, target, faults={"overload_at_s": 2.0} if target == FAULT else None)
        if plant is None:
            bad.append(f"state {target} not reached")
            continue
        plant.force_estop()
        plc.write_inputs(plant.inputs())     # the PLC sees the mushroom at once
        time.sleep(0.1)
        after = []
        seen = plc.pump(plant, 0.8, watch=lambda qx, st: after.append((st, qx["MOTOR_RUN"],
                                                                      qx["P1_VALVE"], qx["P2_VALVE"])))
        if all(st == ESTOP and not (m or p1 or p2) for st, m, p1, p2 in after):
            checked.append(target)
        else:
            bad.append(f"state {target}: {sorted(set(after))}")
    ok = not bad and len(checked) == 7
    return ok, (f"E-Stop pressed in states {checked} -> ESTOP with motor and both valves off; "
                f"problems: {bad or 'none'}")


def tc17(ctx) -> tuple[str, str]:
    """SR-03: hardware independence. Cannot be executed - no kit."""
    return "BLOCKED", ("design evidence only: docs/img/wiring.png puts the E-Stop NC contact in "
                      "series with the K1 coil, so MOTOR_RUN cannot energize the motor while it is "
                      "open. No physical kit exists (docs/SAFETY.md §7), so this case is not "
                      "executed and the report must say so.")


def _recovery(plc: Plc2) -> dict:
    """Trip the E-Stop mid-run, then walk the recovery: release, Reset, Start."""
    plc.boot()
    started = plc.start()
    plant = Plant(boxes=[Box(id=1, size="small"), Box(id=2, size="large")],
                  start_delay_s=0.5)
    plc.pump(plant, 2.5)
    plant.force_estop()
    plc.pump(plant, 0.6)
    out = {"started": started, "trip": (plc.state(), plc.words()["fault_code"],
                                       plc.qx()["MOTOR_RUN"]), "plant": plant}
    plc.pump(plant, 1.0, overrides={"E_STOP_OK": True})     # released, nothing else
    out["released"] = (plc.state(), plc.qx()["MOTOR_RUN"])
    plc.pulse("PB_RESET")
    time.sleep(0.4)
    out["after_reset"] = (plc.state(), plc.words()["fault_code"])
    out["after_start"] = plc.start()
    return out


def tc18(ctx) -> tuple[bool, str]:
    """SR-04: releasing the E-Stop alone must not restart anything."""
    r = _recovery(ctx["plc"])
    ok = (r["started"] and r["trip"][0] == ESTOP and r["released"] == (ESTOP, False))
    return ok, (f"trip {r['trip']}; after releasing the button alone state {r['released'][0]} "
                f"with MOTOR_RUN={r['released'][1]} - it does not resume by itself")


def tc19(ctx) -> tuple[bool, str]:
    """SR-04/FR-12: Reset clears the latch and lands in IDLE, not in RUNNING."""
    r = _recovery(ctx["plc"])
    ok = (r["started"] and r["after_reset"] == (0, 0))
    return ok, (f"after Reset: state {r['after_reset'][0]} (IDLE, not RUNNING), "
                f"fault cleared to {r['after_reset'][1]}")


def tc20(ctx) -> tuple[bool, str]:
    """FR-12: only after the Reset does Start run the line again - two-step recovery."""
    r = _recovery(ctx["plc"])
    blocked = r["released"][0] == ESTOP
    ok = r["started"] and blocked and r["after_start"] and ctx["plc"].state() == RUNNING
    return ok, (f"release alone: no start; Reset then Start: state {ctx['plc'].state()}, "
                f"MOTOR_RUN={ctx['plc'].qx()['MOTOR_RUN']} - two steps, as designed")


def tc21(ctx) -> tuple[bool, str]:
    """SR-06: a pusher may not stroke unless the box it was measured for is still
    in the zone. From the outside a case cannot force an internal state, so this
    plays the two things that are observable: a quiet line, and a stale record.
    """
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    quiet_valve: list[bool] = []
    plc.pump(Plant(boxes=[], start_delay_s=0.0), 3.0,
             watch=lambda qx, st: quiet_valve.append(qx["P1_VALVE"] or qx["P2_VALVE"]))
    quiet_ok = not any(quiet_valve) and plc.state() == RUNNING

    stale_valve: list[bool] = []
    plant = Plant(boxes=[], start_delay_s=0.0)
    hold = {"P1_HOME": False, "P1_EXT": False}          # cylinder 1 is out, cannot stroke
    plc.pump(plant, 0.5, overrides={**hold, "S1_BOX": True, "S1_SIZE": True},
             watch=lambda qx, st: stale_valve.append(qx["P1_VALVE"]))
    plc.pump(plant, 0.3, overrides=hold,                # the box goes away
             watch=lambda qx, st: stale_valve.append(qx["P1_VALVE"]))
    plc.pump(plant, 0.5, overrides={"P1_HOME": True, "P1_EXT": False},
             watch=lambda qx, st: stale_valve.append(qx["P1_VALVE"]))
    w = plc.words()
    stale_ok = w["state"] == FAULT and w["fault_code"] == 8 and not any(stale_valve)
    ok = quiet_ok and stale_ok
    return ok, (f"quiet line, no box: valve never energised={quiet_ok}; "
                f"record with no box in the zone: valve never energised={not any(stale_valve)}, "
                f"state {w['state']}, fault {w['fault_code']} (8 = no box in the pusher zone)")


def tc22(ctx) -> tuple[bool, str]:
    """SR-07: pusher 1 never reaches its extended sensor -> timeout, retract, FAULT."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[Box(id=1, size="large"), Box(id=2, size="small")],
                  start_delay_s=0.5, faults={"cylinder_jam": {"p1": "cannot_extend"}})
    seen: list[bool] = []
    states = plc.pump(plant, 4.0, watch=lambda qx, st: seen.append(qx["P1_VALVE"]))
    w = plc.words()
    ok = (w["state"] == FAULT and w["fault_code"] == 2 and not plc.qx()["MOTOR_RUN"]
          and not plc.qx()["P1_VALVE"] and 4 in states)
    return ok, (f"states {states}; fault {w['fault_code']} (2 = P1 extend timeout); "
                f"valve off, motor off")


def tc23(ctx) -> tuple[bool, str]:
    """SR-07: pusher 2 extends but never comes home -> return timeout, FAULT."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[Box(id=1, size="small"), Box(id=2, size="small", uncertain=True)],
                  start_delay_s=0.5, faults={"cylinder_jam": {"p2": "cannot_return"}})
    states = plc.pump(plant, 8.0)
    w = plc.words()
    ok = (w["state"] == FAULT and w["fault_code"] == 5 and not plc.qx()["MOTOR_RUN"]
          and 6 in states and 7 in states)
    return ok, (f"states {states}; fault {w['fault_code']} (5 = P2 return timeout); "
                f"{w['count_reject']} rejected box(es) already counted, motor off")


def tc24(ctx) -> tuple[bool, str]:
    """SR-05: motor overload stops the sequence and needs a Reset; the Reset only
    works once the cause is gone."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[Box(id=1, size="small")], start_delay_s=0.5,
                  faults={"overload_at_s": 1.5})
    states = plc.pump(plant, 2.5)
    w = plc.words()
    trip = (w["state"], w["fault_code"], plc.qx()["MOTOR_RUN"])
    plc.pulse("PB_RESET")                    # cause still present: must not clear
    time.sleep(0.4)
    still_latched = plc.state() == FAULT
    plc.pump(plant, 0.3, overrides={"MOTOR_OL": True})   # overload clear now
    plc.pulse("PB_RESET")
    time.sleep(0.4)
    cleared = plc.state()
    ok = (trip[0] == FAULT and trip[1] == 6 and not trip[2] and still_latched and cleared == 0)
    return ok, (f"states {states}; trip state {trip[0]} fault {trip[1]} motor {trip[2]}; "
                f"Reset with the overload still open: state {FAULT} (blocked); "
                f"Reset once it cleared -> state {cleared}")


def tc25(ctx) -> tuple[bool, str]:
    """SR-08: the web HMI cannot clear a safety latch. The only coils the HMI can
    write are Start (80) and Stop (81); there is no reset coil to write."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "did not reach RUNNING"
    plant = Plant(boxes=[], start_delay_s=0.0, faults={"overload_at_s": 0.6})
    plc.pump(plant, 1.5)
    latched = plc.state()
    plc.coil(HMI_START_COIL, True)
    plc.coil(HMI_STOP_COIL, True)
    time.sleep(0.5)
    held = (plc.state(), plc.words()["fault_code"])
    plc.coil(HMI_START_COIL, False)
    plc.coil(HMI_STOP_COIL, False)
    time.sleep(0.2)
    ok = latched == FAULT and held[0] == FAULT and held[1] == 6 and not plc.qx()["MOTOR_RUN"]
    return ok, (f"latched in FAULT; HMI Start+Stop written: state {held[0]}, fault {held[1]} "
                f"still standing, motor off. The HMI has no reset coil at all (SR-08), and a "
                f"Reset via the %MX input path is a simulator artefact, not a field one")


def tc26(ctx) -> tuple[bool, str]:
    """SR-02: E-Stop while a pusher is extended. Both valves de-energize and the
    springs bring the cylinders home; the line then recovers with Reset + Start.
    """
    plc, rt = ctx["plc"], ctx["rt"]
    report, good = [], 0
    for target, cyl in ((4, "p1"), (6, "p2")):
        rt.cycle()
        plc.wait_ready()
        plant = _sit_in(plc, target)
        if plant is None:
            report.append(f"state {target} not reached")
            continue
        extended: list[bool] = []
        plc.pump(plant, 0.05, watch=lambda qx, st: extended.append(qx["P1_VALVE"] or qx["P2_VALVE"]))
        plant.force_estop()
        plc.write_inputs(plant.inputs())     # the PLC sees the mushroom at once
        time.sleep(0.1)
        after: list[tuple] = []
        plc.pump(plant, 0.4, watch=lambda qx, st: after.append((st, qx["P1_VALVE"], qx["P2_VALVE"])))
        plc.pump(plant, 1.5)                     # springs bring both cylinders home
        home = all((plant.p1.home, plant.p2.home))
        plc.pump(plant, 0.2, overrides={"E_STOP_OK": True})
        plc.pulse("PB_RESET")
        time.sleep(0.4)
        recovered = plc.start() and plc.state() == RUNNING
        case_ok = (any(extended) and after and all(st == ESTOP for st, _, _ in after)
                   and not any(p1 or p2 for _, p1, p2 in after) and home and recovered)
        good += 1 if case_ok else 0
        report.append(f"state {target}: valve was on={any(extended)}, valves off after the trip="
                      f"{not any(p1 or p2 for _, p1, p2 in after)}, both cylinders home={home}, "
                      f"Reset+Start recovers={recovered}")
    ok = good == 2
    return ok, "; ".join(report) if report else "no state reached"



def tc27(ctx) -> tuple[bool, str]:
    """FR-11: the HMI shows the same state, counters and lamps as the runtime."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "PLC did not reach RUNNING"
    time.sleep(0.3)
    ok, detail = ctx["hmi"].matches(plc)
    return ok, detail


def tc28(ctx) -> tuple[bool, str]:
    """FR-11/FR-10: 3 boxes, then the HMI counters equal the runtime's within 1 s."""
    plc, rt, hmi = ctx["plc"], ctx["rt"], ctx["hmi"]
    rt.cycle()
    plc.wait_ready()
    plc.boot()
    if not plc.start():
        return False, "PLC did not reach RUNNING"
    r = plc.feed([Box(id=1, size="small"), Box(id=2, size="large"),
                  Box(id=3, size="small", uncertain=True)])
    want = {"CNT_SMALL": r["words"]["count_small"],
            "CNT_LARGE": r["words"]["count_large"],
            "CNT_REJECT": r["words"]["count_reject"]}
    # the HMI polls once a second; give it one poll and measure what it took
    t0 = time.time()
    shown: dict = {}
    while time.time() - t0 < 3.0:
        shown = hmi.snapshot()
        if all(shown.get(k) == want[k] for k in ("CNT_SMALL", "CNT_LARGE", "CNT_REJECT")):
            break
        time.sleep(0.2)
    lag = round(time.time() - t0, 2)
    ok = (all(shown.get(k) == want[k] for k in ("CNT_SMALL", "CNT_LARGE", "CNT_REJECT"))
          and lag <= 1.5)
    return ok, (f"PLC {want['CNT_SMALL']}/{want['CNT_LARGE']}/{want['CNT_REJECT']}, "
                f"HMI {shown.get('CNT_SMALL')}/{shown.get('CNT_LARGE')}/"
                f"{shown.get('CNT_REJECT')}, matched in {lag} s")


def tc29(ctx) -> tuple[bool, str]:
    """SR-08: losing the HMI changes nothing. Stop FUXA, run the line by hand,
    start FUXA again: the PLC never stopped and never restarted."""
    plc = ctx["plc"]
    plc.boot()
    if not plc.start():
        return False, "PLC did not reach RUNNING"
    before = plc.read_words()
    subprocess.run(["docker", "compose", "stop", "hmi"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    try:
        time.sleep(2)
        # the panel keeps working with no HMI: stop, reset, start from the field
        plc.pulse("PB_STOP")
        time.sleep(0.5)
        plc.boot()
        restarted = plc.start()
        running = plc.state() == RUNNING
        counters_kept = plc.read_words()["count_small"] == before["count_small"]
    finally:
        subprocess.run(["docker", "compose", "start", "hmi"], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
    # wait for the HMI to come back and agree with the PLC again
    end = time.time() + 40
    back, detail = False, "HMI did not reconnect"
    while time.time() < end:
        try:
            back, detail = ctx["hmi"].matches(plc)
            if back:
                break
        except Exception:
            pass
        time.sleep(1)
    ok = restarted and running and counters_kept and back
    return ok, (f"FUXA down 2 s: line stopped and restarted from the panel only "
                f"(RUNNING={running}), counters kept={counters_kept}; "
                f"HMI back and agreeing={back}")


def tc30(ctx) -> tuple[bool, str]:
    """SR-08: the screen may start and stop the line, never reset a latch."""
    plc, rt = ctx["plc"], ctx["rt"]
    rt.cycle()
    plc.wait_ready()
    plc.boot()
    plc.coil(HMI_START_COIL, True)
    time.sleep(0.3)
    plc.coil(HMI_START_COIL, False)
    started = plc.state() == RUNNING
    plc.coil(HMI_STOP_COIL, True)
    time.sleep(0.5)
    plc.coil(HMI_STOP_COIL, False)
    stopped = plc.state() != RUNNING
    project = requests.get("http://localhost:1881/api/project", timeout=10).json()
    widgets = [it for view in project.get("hmi", {}).get("views", [])
               for it in view.get("items", {}).values()]
    reset_widgets = [w for w in widgets if "RESET" in str(w.get("property", {}).get("variable", ""))]
    ok = started and stopped and not reset_widgets
    return ok, (f"HMI Start from IDLE={started}, HMI Stop={stopped}, "
                f"reset widgets on the screen={len(reset_widgets)} (must be 0)")

CASES = [("TC-01", tc01), ("TC-02", tc02), ("TC-03", tc03), ("TC-04", tc04),
         ("TC-05", tc05), ("TC-06", tc06), ("TC-07", tc07), ("TC-08", tc08),
         ("TC-09", tc09), ("TC-10", tc10), ("TC-11", tc11), ("TC-12", tc12),
         ("TC-13", tc13), ("TC-14", tc14), ("TC-15", tc15), ("TC-16", tc16),
         ("TC-17", tc17), ("TC-18", tc18), ("TC-19", tc19), ("TC-20", tc20),
         ("TC-21", tc21), ("TC-22", tc22), ("TC-23", tc23), ("TC-24", tc24),
         ("TC-25", tc25), ("TC-26", tc26), ("TC-27", tc27), ("TC-28", tc28),
         ("TC-29", tc29), ("TC-30", tc30)]


def main() -> int:
    wanted = [a for a in sys.argv[1:] if a.startswith("TC-")]
    rt = Runtime()
    plc = Plc2("127.0.0.1", 502)
    plc.wait_ready()
    rows, failed, blocked = [], 0, 0
    try:
        for cid, fn in CASES:
            if wanted and cid not in wanted:
                continue
            if cid != "TC-01":
                rt.cycle()
                plc.wait_ready()
            ctx = {"plc": plc, "rt": rt, "hmi": Hmi()}
            try:
                result, detail = fn(ctx)
            except AssertionError as exc:
                result, detail = False, f"assertion: {exc}"
            if result is True:
                verdict = "PASS"
            elif result == "BLOCKED":
                verdict, blocked = "BLOCKED", blocked + 1
            else:
                verdict, failed = "FAIL", failed + 1
            rows.append((cid, verdict, detail))
            print(f"{cid}  {verdict}  {detail}")
    finally:
        plc.close()

    log = ROOT / "docs/TEST-LOG.md"
    existing = log.read_text() if log.exists() else ""
    section = ["", f"## Run {time.strftime('%Y-%m-%d %H:%M')} — sim/tests.py", "",
               "| Case | Result | Evidence |", "|------|--------|----------|"]
    section += [f"| {cid} | {res} | {det} |" for cid, res, det in rows]
    passed = sum(1 for r in rows if r[1] == "PASS")
    section += ["", f"{passed}/{len(rows)} passed, {blocked} blocked (no hardware), {failed} failed.", ""]
    log.write_text(existing + "\n".join(section))
    print(f"\n{passed}/{len(rows)} passed, {blocked} blocked, {failed} failed -> docs/TEST-LOG.md")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())