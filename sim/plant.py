"""Plant simulator for the DecodeLabs Project 4 conveyor sorter.

The PLC is the Modbus TCP server (OpenPLC Runtime v4, port 502). This script is
the plant: it writes the sensor/button bits and reads the actuator bits plus the
HMI words.

Why the sensors are written as coils and not as %IX
---------------------------------------------------
This build is a soft PLC: the I/O image is delivered over Modbus by the
project's own Modbus server, and no producer exists for %IX on this target. The
editor therefore sizes the *memory* bit area, and the plant writes into it:

  coils   0..5            %QX0.0..   MOTOR_RUN, P1_VALVE, P2_VALVE,
                                     LAMP_RUN, LAMP_FAULT, LAMP_REJ
  coils  64..77           %MX0.0..   digital inputs, see MX_BITS below
  coils  80..81           %MX2.0..   HMI_START, HMI_STOP
  holding registers 0..4  %QW0..     state, count_small, count_large,
                                     count_reject, fault_code

On real hardware with an input card the same tags land on %IX and nothing else
changes. See docs/ARCHITECTURE.md.

Run:
    python sim/plant.py --scenario sim/scenarios/acceptance-10.json
    python sim/plant.py --scenario sim/scenarios/faults-safety.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from pymodbus.client import ModbusTcpClient

# --- address map -----------------------------------------------------------
MX_BASE = 64
QX_BASE = 0
QX_NAMES = ["MOTOR_RUN", "P1_VALVE", "P2_VALVE", "LAMP_RUN", "LAMP_FAULT", "LAMP_REJ"]
QW_NAMES = ["state", "count_small", "count_large", "count_reject", "fault_code"]

MX_BITS = {
    "E_STOP_OK": 0,
    "PB_START": 1,
    "PB_STOP": 2,
    "PB_RESET": 3,
    "S1_BOX": 4,
    "S1_SIZE": 5,
    "S2_BOX": 6,
    "P1_HOME": 7,
    "P1_EXT": 8,
    "P2_HOME": 9,
    "P2_EXT": 10,
    "MOTOR_OL": 11,
    "MODE_AUTO": 12,
    "S2_CLEAR": 13,
}

# --- plant geometry (assumption, see docs/DECISIONS.md Q12/Q13) -------------
STATION1_MM = 600.0        # pusher 1, where the size is measured
STATION2_MM = 1200.0       # pusher 2, the reject lane
BOX_LEN_MM = 200.0
SENSOR_WINDOW_MM = 150.0   # a box counts as "at the station" inside this window
CYL_TRAVEL_S = 0.25
TICK_S = 0.05
LARGE_MM = 100.0           # threshold, sensor at 60 mm


@dataclass
class Box:
    id: int
    size: str                  # "small" | "large"  (the physical truth)
    uncertain: bool = False    # height reading is not trustworthy -> must reject
    expect_lane: str | None = None   # scenario-declared safe outcome, if any
    x: float = -BOX_LEN_MM
    lane: str | None = None
    entry_t: float | None = None
    done_t: float | None = None

    @property
    def tall(self) -> bool:
        return self.size == "large"

    @property
    def expected(self) -> str:
        if self.expect_lane:
            return self.expect_lane
        return "reject" if self.uncertain else self.size


@dataclass
class Cylinder:
    home: bool = True
    ext: bool = False
    want_ext: bool = False
    moving_since: float | None = None


@dataclass
class Plant:
    boxes: list[Box]
    speed_mm_s: float = 300.0
    pitch_mm: float = 400.0
    start_delay_s: float = 1.0
    faults: dict = field(default_factory=dict)
    p1: Cylinder = field(default_factory=Cylinder)
    p2: Cylinder = field(default_factory=Cylinder)
    t: float = 0.0
    spawn_idx: int = 0
    events: list[str] = field(default_factory=list)
    faults_fired: list[str] = field(default_factory=list)

    # --- fault injection ---------------------------------------------------
    def _fired(self, name: str, when) -> bool:
        """A fault flag is either a start time (seconds) or a plain bool."""
        if when is None or when is False:
            return False
        if when is True:
            return True
        if self.t >= float(when) and name not in self.faults_fired:
            self.faults_fired.append(name)
            self.events.append(f"t={self.t:6.2f} FAULT INJECTED: {name}")
        return self.t >= float(when)

    def estop_active(self) -> bool:
        return self._fired("e-stop pressed", self.faults.get("estop_at_s"))

    def overload_active(self) -> bool:
        return self._fired("motor overload", self.faults.get("overload_at_s"))

    def s1_eye_dead(self) -> bool:
        return self._fired("station 1 photo-eye dead",
                           self.faults.get("s1_eye_dead_at_s"))

    # --- motion ------------------------------------------------------------
    def spawn_due(self) -> None:
        while self.spawn_idx < len(self.boxes):
            due = self.start_delay_s + self.spawn_idx * self.pitch_mm / self.speed_mm_s
            if self.t >= due:
                b = self.boxes[self.spawn_idx]
                b.entry_t = self.t
                self.spawn_idx += 1
                self.events.append(f"t={self.t:6.2f} box {b.id} entered the belt")
            else:
                break

    def step(self, dt: float, qx: dict[str, bool]) -> None:
        self.t += dt
        self.spawn_due()

        if self.estop_active() or self.overload_active():
            # the motor contactor is out: everything on the belt stops dead
            return

        for b in self.boxes:
            if b.lane is None and b.id <= self.spawn_idx:
                b.x += self.speed_mm_s * dt

        jam = self.faults.get("cylinder_jam") or {}
        for name, cyl, valve, station in (("p1", self.p1, qx["P1_VALVE"], STATION1_MM),
                                          ("p2", self.p2, qx["P2_VALVE"], STATION2_MM)):
            if valve != cyl.want_ext:
                cyl.want_ext = valve
                cyl.moving_since = self.t
            if cyl.moving_since is None or self.t - cyl.moving_since < CYL_TRAVEL_S:
                continue
            cyl.moving_since = None
            if jam.get(name) == "cannot_extend" and cyl.want_ext:
                self.events.append(f"t={self.t:6.2f} JAM: {name} will not extend")
                continue                     # stuck retracted -> extend timeout
            if jam.get(name) == "cannot_return" and not cyl.want_ext:
                self.events.append(f"t={self.t:6.2f} JAM: {name} will not return")
                continue                     # stuck extended -> return timeout
            cyl.home, cyl.ext = not cyl.want_ext, cyl.want_ext

        for b in self.boxes:
            if b.lane is not None or b.id > self.spawn_idx:
                continue
            if self.p1.ext and abs(b.x - STATION1_MM) < SENSOR_WINDOW_MM:
                b.lane, b.done_t = "large", self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} diverted LARGE by pusher 1")
            elif self.p2.ext and abs(b.x - STATION2_MM) < SENSOR_WINDOW_MM:
                b.lane, b.done_t = "reject", self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} diverted REJECT by pusher 2")
            elif b.x > STATION2_MM + 3 * SENSOR_WINDOW_MM:
                # left straight, no actuator fired: the small lane
                b.lane, b.done_t = "small", self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} left straight (small lane)")

    # --- sensor image ------------------------------------------------------
    def inputs(self) -> dict[str, bool]:
        def at(station: float, tol: float = SENSOR_WINDOW_MM) -> list[Box]:
            return [b for b in self.boxes if b.lane is None and b.id <= self.spawn_idx
                    and abs(b.x - station) < tol]

        s1, s2 = at(STATION1_MM), at(STATION2_MM)

        # Presence eye works unless faulted; an "uncertain" box is one whose
        # height eye asserts at the edge of the window and has dropped by the
        # time the second sample is taken. The controller must reject that.
        size = False
        if s1 and not self.s1_eye_dead():
            b = s1[0]
            size = (b.x < STATION1_MM - SENSOR_WINDOW_MM + 20.0) if b.uncertain else b.tall

        return {
            "E_STOP_OK": not self.estop_active(),
            "PB_START": False,
            "PB_STOP": True,          # NC
            "PB_RESET": False,
            "S1_BOX": bool(s1) and not self.s1_eye_dead(),
            "S1_SIZE": size,
            "S2_BOX": bool(s2),
            "P1_HOME": self.p1.home,
            "P1_EXT": self.p1.ext,
            "P2_HOME": self.p2.home,
            "P2_EXT": self.p2.ext,
            "MOTOR_OL": not self.overload_active(),   # NC
            "MODE_AUTO": True,
            "S2_CLEAR": bool(s2) and s2[0].x > STATION2_MM,
        }

    def done(self) -> bool:
        if self.estop_active() or self.overload_active():
            return False              # those runs end on the fault, not on lanes
        return self.spawn_idx >= len(self.boxes) and all(b.lane is not None for b in self.boxes)

    # --- reporting ---------------------------------------------------------
    def box_lines(self) -> list[str]:
        out = ["", "box  truth   expect   lane     entry    done"]
        for b in self.boxes:
            out.append(f"{b.id:3d}  {b.size:7s} {b.expected:8s} "
                       f"{b.lane or '-':8s} {b.entry_t if b.entry_t is not None else '-':>7} "
                       f"{b.done_t if b.done_t is not None else '-':>7}")
        return out

    def mis_sorts(self) -> list[str]:
        bad = []
        for b in self.boxes:
            if b.lane is None:
                bad.append(f"box {b.id}: never reached a lane (expected {b.expected})")
            elif b.lane != b.expected:
                bad.append(f"box {b.id}: expected {b.expected}, got {b.lane}")
        return bad


class Plc:
    def __init__(self, host: str, port: int, device_id: int = 1):
        self.host, self.port = host, port
        self.c = ModbusTcpClient(host, port=port, timeout=2)
        if not self.c.connect():
            raise SystemExit(f"cannot reach PLC at {host}:{port}")
        self.device_id = device_id
        # one image, written in one request: two separate coil writes could be
        # caught by a PLC scan in between, and the controller would then latch a
        # size reading that does not belong to the box it just detected
        self._image = [False] * len(MX_BITS)

    def _retry(self, fn, attempts: int = 8):
        """The runtime stops serving Modbus while it reloads - back off and retry.

        A reload can accept the TCP connection and then reset it, so one retry is
        not enough.
        """
        for i in range(attempts):
            try:
                return fn()
            except Exception:
                if i == attempts - 1:
                    raise
                self.c.close()
                time.sleep(0.5)
                try:
                    self.c.connect()
                except Exception:
                    pass

    def _flush(self) -> None:
        """Publish the whole input image in one Modbus request."""
        self._retry(lambda: self.c.write_coils(MX_BASE, list(self._image),
                                              device_id=self.device_id))

    def write_inputs(self, values: dict[str, bool]) -> None:
        if self._update(values):
            self._flush()

    def _update(self, values: dict[str, bool]) -> bool:
        changed = False
        for name, val in values.items():
            i = MX_BITS[name]
            if self._image[i] != bool(val):
                self._image[i] = bool(val)
                changed = True
        return changed

    def read_outputs(self) -> dict[str, bool]:
        r = self._retry(lambda: self.c.read_coils(QX_BASE, count=6, device_id=self.device_id))
        return dict(zip(QX_NAMES, r.bits[:6]))

    def read_words(self) -> dict[str, int]:
        r = self._retry(lambda: self.c.read_holding_registers(0, count=5,
                                                             device_id=self.device_id))
        return dict(zip(QW_NAMES, r.registers))

    def set_input(self, name: str, value: bool) -> None:
        if self._update({name: value}):
            self._flush()

    def wait_ready(self, timeout_s: float = 30.0) -> None:
        """After a stop/start the runtime rebinds Modbus; wait until it answers."""
        deadline = time.time() + timeout_s
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self.read_words()
                return
            except Exception as exc:      # noqa: BLE001 - any transport error
                last = exc
            time.sleep(1.0)
        raise SystemExit(f"PLC Modbus did not become ready in {timeout_s}s ({last})")

    def pulse(self, name: str) -> None:
        self.set_input(name, True)
        time.sleep(0.15)
        self.set_input(name, False)

    def close(self) -> None:
        self.c.close()


def load_scenario(path: Path) -> tuple[list[Box], dict]:
    spec = json.loads(Path(path).read_text())
    boxes = [Box(id=b.get("id", i + 1), size=b["size"],
                 uncertain=b.get("uncertain", b.get("miss", False)),
                 expect_lane=b.get("expect_lane"))
             for i, b in enumerate(spec.get("boxes", []))]
    return boxes, spec


def main() -> int:
    ap = argparse.ArgumentParser(description="conveyor plant simulator (Modbus TCP client)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--speed", type=float, default=300.0)
    ap.add_argument("--pitch", type=float, default=400.0)
    ap.add_argument("--log", default="sim/run.log")
    ap.add_argument("--max-s", type=float, default=120.0)
    ap.add_argument("--no-start", action="store_true", help="do not press Reset/Start")
    args = ap.parse_args()

    boxes, spec = load_scenario(Path(args.scenario))
    plant_cfg = spec.get("plant", {})
    expect = dict(spec.get("expect", {}))
    expect.update(spec.get("expect_counts", {}))
    fault_expect = "state" in expect or "fault_code" in expect

    plant = Plant(boxes=boxes,
                  speed_mm_s=args.speed,
                  pitch_mm=args.pitch,
                  start_delay_s=plant_cfg.get("start_delay_s", 1.0),
                  faults=spec.get("faults", {}))
    plc = Plc(args.host, args.port)
    plc.wait_ready()
    print(f"scenario {spec.get('name', args.scenario)}: "
          f"{len(boxes)} box(es), faults={spec.get('faults', {}) or 'none'}")

    try:
        for name in ("E_STOP_OK", "PB_STOP", "P1_HOME", "P2_HOME", "MOTOR_OL", "MODE_AUTO"):
            plc.set_input(name, True)
        time.sleep(0.3)
        if not args.no_start:
            plc.pulse("PB_RESET")
            time.sleep(0.3)
            plc.pulse("PB_START")
            time.sleep(0.3)
        if plc.read_words()["state"] != 2:
            # a log even when the run never started, otherwise the harness
            # looks like it silently did nothing
            return finish([f"scenario: {spec.get('name', args.scenario)}",
                           f"PLC did not reach RUNNING: {plc.read_words()}",
                           "RESULT: FAIL"], args.log)

        # when the scenario expects a fault, keep ticking after the last box has
        # landed - the fault is what we are waiting for
        while plant.t < args.max_s and (fault_expect or not plant.done()):
            plant.step(TICK_S, plc.read_outputs())
            plc.write_inputs(plant.inputs())
            if expect and _settled(plc, expect):
                break
            time.sleep(TICK_S)

        words = plc.read_words()
        qx = plc.read_outputs()
        lines = [f"scenario: {spec.get('name', args.scenario)}",
                 f"note: {spec.get('note', '')}"]
        lines += plant.events
        lines += plant.box_lines()
        lines.append("")
        lines.append(f"PLC words : {words}")
        lines.append(f"PLC coils : {qx}")

        ok = True
        if not fault_expect:
            bad = plant.mis_sorts()
            ok = not bad
            if bad:
                lines.append("MIS-SORTS:")
                lines += [f"  {b}" for b in bad]
        if expect:
            for key in ("state", "fault_code", "count_small", "count_large", "count_reject"):
                if key in expect and words[key] != expect[key]:
                    ok = False
                    lines.append(f"EXPECT FAIL: {key} expected {expect[key]}, got {words[key]}")
            if expect.get("actuators_off") and (qx["MOTOR_RUN"] or qx["P1_VALVE"] or qx["P2_VALVE"]):
                ok = False
                lines.append(f"EXPECT FAIL: actuators must be off, got {qx}")
            lines.append(f"expect: {expect}")

        lines.append(f"RESULT: {'PASS' if ok else 'FAIL'}")
        return finish(lines, args.log)
    finally:
        plc.close()


def finish(lines: list[str], log_path: str) -> int:
    text = "\n".join(lines)
    print(text)
    Path(log_path).write_text(text + "\n")
    return 0 if lines and lines[-1] == "RESULT: PASS" else 1


def _settled(plc: Plc, expect: dict) -> bool:
    """Stop early once the PLC has latched the fault we were injecting."""
    try:
        w = plc.read_words()
    except Exception:
        return False
    if "fault_code" in expect and w["fault_code"] == expect["fault_code"]:
        return True
    if expect.get("state") in (9, 10) and w["state"] in (9, 10):
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
