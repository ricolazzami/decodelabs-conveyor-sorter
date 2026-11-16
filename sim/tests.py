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

    def pulse_ms(self, name: str, ms: int) -> None:
        self.w(**{name: True})
        time.sleep(ms / 1000.0)
        self.w(**{name: False})


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


CASES = [("TC-01", tc01), ("TC-02", tc02), ("TC-03", tc03), ("TC-04", tc04),
         ("TC-05", tc05), ("TC-06", tc06), ("TC-07", tc07), ("TC-08", tc08),
         ("TC-09", tc09), ("TC-10", tc10), ("TC-11", tc11), ("TC-12", tc12),
         ("TC-13", tc13), ("TC-14", tc14)]


def main() -> int:
    wanted = [a for a in sys.argv[1:] if a.startswith("TC-")]
    rt = Runtime()
    plc = Plc2("127.0.0.1", 502)
    plc.wait_ready()
    rows, failed = [], 0
    try:
        for cid, fn in CASES:
            if wanted and cid not in wanted:
                continue
            if cid != "TC-01":
                rt.cycle()
                plc.wait_ready()
            ctx = {"plc": plc, "rt": rt}
            try:
                ok, detail = fn(ctx)
            except AssertionError as exc:
                ok, detail = False, f"assertion: {exc}"
            failed += 0 if ok else 1
            rows.append((cid, "PASS" if ok else "FAIL", detail))
            print(f"{cid}  {'PASS' if ok else 'FAIL'}  {detail}")
    finally:
        plc.close()

    log = ROOT / "docs/TEST-LOG.md"
    existing = log.read_text() if log.exists() else ""
    section = ["", f"## Run {time.strftime('%Y-%m-%d %H:%M')} — sim/tests.py", "",
               "| Case | Result | Evidence |", "|------|--------|----------|"]
    section += [f"| {cid} | {res} | {det} |" for cid, res, det in rows]
    section += ["", f"{sum(1 for r in rows if r[1] == 'PASS')}/{len(rows)} passed.", ""]
    log.write_text(existing + "\n".join(section))
    print(f"\n{sum(1 for r in rows if r[1] == 'PASS')}/{len(rows)} passed -> docs/TEST-LOG.md")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())