"""Plant simulator for the DecodeLabs Project 4 conveyor sorter.

The PLC is the Modbus TCP server (OpenPLC Runtime v4, port 502). This script is
the plant: it writes the sensor/button coils (%MX region) and reads the actuator
coils (%QX) plus the HMI words (%QW holding registers).

Address map (see docs/ARCHITECTURE.md):
  coils   0..5   %QX0.0..   MOTOR_RUN, P1_VALVE, P2_VALVE, LAMP_RUN, LAMP_FAULT, LAMP_REJ
  coils  64..77  %MX0.0..   digital inputs, in the order of MX_BITS below
  HR      0..4   %QW0..     state, count_small, count_large, count_reject, fault_code

Run:
    python sim/plant.py --scenario sim/scenarios/acceptance-10.json
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

# --- plant geometry --------------------------------------------------------
STATION1_MM = 600.0
STATION2_MM = 1200.0
BOX_LEN_MM = 200.0
SENSOR_WINDOW_MM = 150.0  # a box counts as "at the station" inside this window
CYL_TRAVEL_S = 0.25
TICK_S = 0.05


@dataclass
class Box:
    id: int
    size: str  # "small" | "large"
    miss: bool = False  # sensor miss at station 1 -> must end up in reject
    x: float = -BOX_LEN_MM
    lane: str | None = None
    t_done: float | None = None

    @property
    def tall(self) -> bool:
        return self.size == "large"


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
    p1: Cylinder = field(default_factory=Cylinder)
    p2: Cylinder = field(default_factory=Cylinder)
    t: float = 0.0
    spawn_idx: int = 0
    events: list[str] = field(default_factory=list)

    def spawn_due(self) -> None:
        while self.spawn_idx < len(self.boxes):
            due = self.start_delay_s + self.spawn_idx * self.pitch_mm / self.speed_mm_s
            if self.t >= due:
                self.events.append(f"t={self.t:6.2f} box {self.boxes[self.spawn_idx].id} entered the belt")
                self.spawn_idx += 1
            else:
                break

    def step(self, dt: float, qx: dict[str, bool]) -> None:
        self.t += dt
        self.spawn_due()

        for b in self.boxes:
            if b.lane is None and b.id <= self.spawn_idx:
                b.x += self.speed_mm_s * dt

        for cyl, valve, station in ((self.p1, qx["P1_VALVE"], STATION1_MM),
                                    (self.p2, qx["P2_VALVE"], STATION2_MM)):
            if valve != cyl.want_ext:
                cyl.want_ext = valve
                cyl.moving_since = self.t
            if cyl.moving_since is not None and self.t - cyl.moving_since >= CYL_TRAVEL_S:
                cyl.home, cyl.ext = not cyl.want_ext, cyl.want_ext
                cyl.moving_since = None
        # a box in the station window gets pushed when the cylinder is out
        for b in self.boxes:
            if b.lane is not None or b.id > self.spawn_idx:
                continue
            if self.p1.ext and abs(b.x - STATION1_MM) < SENSOR_WINDOW_MM:
                b.lane = "large"
                b.t_done = self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} diverted LARGE by pusher 1")
            elif self.p2.ext and abs(b.x - STATION2_MM) < SENSOR_WINDOW_MM:
                b.lane = "reject"
                b.t_done = self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} diverted REJECT by pusher 2")
            elif b.x > STATION2_MM + 3 * SENSOR_WINDOW_MM:
                b.lane = "small"          # left straight, no actuator fired
                b.t_done = self.t
                self.events.append(f"t={self.t:6.2f} box {b.id} left straight (small lane)")

    def inputs(self) -> dict[str, bool]:
        def at(station: float, tol: float = SENSOR_WINDOW_MM) -> list[Box]:
            return [b for b in self.boxes if b.lane is None and b.id <= self.spawn_idx
                    and abs(b.x - station) < tol]

        s1, s2 = at(STATION1_MM), at(STATION2_MM)

        # Presence sensor works; a sensor "miss" is an uncertain height reading:
        # the photo-eye asserts for part of the window then drops, so a sample at
        # the edge and a sample 200 ms later disagree. The PLC must reject that.
        size = False
        if s1:
            b = s1[0]
            if b.miss:
                size = b.x < STATION1_MM - SENSOR_WINDOW_MM + 20.0
            else:
                size = b.tall

        return {
            "E_STOP_OK": True,
            "PB_START": False,
            "PB_STOP": True,     # NC
            "PB_RESET": False,
            "S1_BOX": bool(s1),
            "S1_SIZE": size,
            "S2_BOX": bool(s2),
            "P1_HOME": self.p1.home,
            "P1_EXT": self.p1.ext,
            "P2_HOME": self.p2.home,
            "P2_EXT": self.p2.ext,
            "MOTOR_OL": True,    # NC
            "MODE_AUTO": True,
            "S2_CLEAR": bool(s2) and s2[0].x > STATION2_MM,
        }

    def done(self) -> bool:
        return self.spawn_idx >= len(self.boxes) and all(b.lane is not None for b in self.boxes)

    def verdict(self) -> list[str]:
        expected = {"small": "small", "large": "large"}
        bad = []
        for b in self.boxes:
            want = "reject" if b.miss else expected[b.size]
            if b.lane != want:
                bad.append(f"box {b.id}: expected {want}, got {b.lane}")
        return bad


class Plc:
    def __init__(self, host: str, port: int, device_id: int = 1):
        self.host, self.port = host, port
        self.c = ModbusTcpClient(host, port=port, timeout=2)
        if not self.c.connect():
            raise SystemExit(f"cannot reach PLC at {host}:{port}")
        self.device_id = device_id
        self._written: dict[str, bool] = {}

    def _retry(self, fn):
        """PLC stops serving Modbus while it restarts - reconnect once."""
        try:
            return fn()
        except Exception:
            self.c.close()
            time.sleep(0.5)
            if not self.c.connect():
                raise
            return fn()

    def write_inputs(self, values: dict[str, bool]) -> None:
        for name, val in values.items():
            if self._written.get(name) != val:
                self._retry(lambda n=name, v=bool(val):
                            self.c.write_coil(MX_BASE + MX_BITS[n], v, device_id=self.device_id))
                self._written[name] = bool(val)

    def read_outputs(self) -> dict[str, bool]:
        r = self._retry(lambda: self.c.read_coils(QX_BASE, count=6, device_id=self.device_id))
        names = ["MOTOR_RUN", "P1_VALVE", "P2_VALVE", "LAMP_RUN", "LAMP_FAULT", "LAMP_REJ"]
        return dict(zip(names, r.bits[:6]))

    def read_words(self) -> dict[str, int]:
        r = self._retry(lambda: self.c.read_holding_registers(0, count=5, device_id=self.device_id))
        names = ["state", "count_small", "count_large", "count_reject", "fault_code"]
        return dict(zip(names, r.registers))

    def set_input(self, name: str, value: bool) -> None:
        self._retry(lambda: self.c.write_coil(MX_BASE + MX_BITS[name], bool(value),
                                             device_id=self.device_id))
        self._written[name] = bool(value)

    def pulse(self, name: str) -> None:
        self.set_input(name, True)
        time.sleep(0.15)
        self.set_input(name, False)

    def close(self) -> None:
        self.c.close()


def load_scenario(path: Path) -> tuple[list[Box], dict]:
    spec = json.loads(Path(path).read_text())
    boxes = [Box(id=b.get("id", i + 1), size=b["size"], miss=b.get("miss", False))
             for i, b in enumerate(spec.get("boxes", []))]
    return boxes, spec.get("plant", {})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--speed", type=float, default=300.0)
    ap.add_argument("--pitch", type=float, default=400.0)
    ap.add_argument("--log", default="sim/run.log")
    ap.add_argument("--max-s", type=float, default=120.0)
    ap.add_argument("--no-start", action="store_true", help="do not press Start/Reset")
    args = ap.parse_args()

    boxes, plant_cfg = load_scenario(Path(args.scenario))
    plant = Plant(boxes=boxes, speed_mm_s=args.speed, pitch_mm=args.pitch,
                  start_delay_s=plant_cfg.get("start_delay_s", 1.0))
    plc = Plc(args.host, args.port)

    try:
        # healthy chain, then Reset, then Start
        for name in ("E_STOP_OK", "PB_STOP", "P1_HOME", "P2_HOME", "MOTOR_OL", "MODE_AUTO"):
            plc.set_input(name, True)
        time.sleep(0.3)
        if not args.no_start:
            plc.pulse("PB_RESET")
            time.sleep(0.3)
            plc.pulse("PB_START")
            time.sleep(0.3)
        if plc.read_words()["state"] != 2:
            print("PLC did not reach RUNNING:", plc.read_words())
            return 2

        while plant.t < args.max_s and not plant.done():
            plant.step(TICK_S, plc.read_outputs())
            plc.write_inputs(plant.inputs())
            time.sleep(TICK_S)

        words = plc.read_words()
        bad = plant.verdict()
        lines = plant.events + [
            "",
            f"PLC words: {words}",
        ]
        if bad:
            lines.append("MIS-SORTS:")
            lines += [f"  {b}" for b in bad]
        else:
            lines.append("VERDICT: PASS - every box landed in the expected lane")
        lines.append(f"PLC counters expect small={sum(1 for b in boxes if b.lane=='small')} "
                     f"large={sum(1 for b in boxes if b.lane=='large')} "
                     f"reject={sum(1 for b in boxes if b.lane=='reject')}")
        text = "\n".join(lines)
        print(text)
        Path(args.log).write_text(text + "\n")
        return 0 if not bad else 1
    finally:
        plc.close()


if __name__ == "__main__":
    raise SystemExit(main())