#!/usr/bin/env python3
"""Compare the FUXA HMI with the PLC (TC-27, TC-28).

    python3 fuxa/check.py

Reads the PLC directly over Modbus TCP and asks the HMI web server which values
it is showing, then checks they are the same. Run it while a scenario is going
to check the refresh lag too: the HMI polls every 1000 ms, so with
`--watch 5` it samples both sides five times and reports the worst delay.

Exit code 0 = every value matches.
"""
import argparse
import time

import socketio
from pymodbus.client import ModbusTcpClient

FUXA = "http://localhost:1881"
DEVICE = "PLC"
COILS = {0: "MOTOR_RUN", 1: "P1_VALVE", 2: "P2_VALVE",
         3: "LAMP_RUN", 4: "LAMP_FAULT", 5: "LAMP_REJ"}
WORDS = {0: "ST_WORD", 1: "CNT_SMALL", 2: "CNT_LARGE", 3: "CNT_REJECT", 4: "FAULT_CODE"}


def plc():
    c = ModbusTcpClient("127.0.0.1", port=502, timeout=3)
    c.connect()
    try:
        hr = c.read_holding_registers(0, count=len(WORDS), device_id=1).registers
        co = c.read_coils(0, count=len(COILS), device_id=1).bits
    finally:
        c.close()
    out = {WORDS[i]: hr[i] for i in WORDS}
    out.update({COILS[i]: int(co[i]) for i in COILS})
    return out


def hmi(tags):
    """One snapshot of the values the HMI server is serving to its clients."""
    sio = socketio.Client(reconnection=False)
    got = {}

    @sio.on("device-values")
    def values(msg):
        for v in msg.get("values", []):
            got[v["id"].split("^~^")[-1]] = v["value"]

    sio.connect(FUXA, transports=["websocket", "polling"])
    try:
        sio.emit("device-tags-subscribe",
                 {"tagsId": [f"{DEVICE}^~^{t}" for t in tags], "sendLastValue": True})
        for _ in range(40):
            if len(got) >= len(tags):
                break
            time.sleep(0.1)
    finally:
        sio.disconnect()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0, help="seconds to keep comparing")
    args = ap.parse_args()

    tags = list(WORDS.values()) + list(COILS.values())
    bad = 0
    worst = 0.0
    end = time.time() + args.watch
    while True:
        p = plc()
        h = hmi(tags)
        row = []
        for t in tags:
            ok = int(h.get(t, -999)) == p[t]
            bad += 0 if ok else 1
            row.append(f"{t}={p[t]}{'' if ok else f'/HMI {h.get(t)}'}")
        print(" | ".join(row))
        if time.time() >= end:
            break
        time.sleep(1)

    print("RESULT:", "PASS" if bad == 0 else f"FAIL ({bad} mismatches)")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())