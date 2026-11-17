#!/usr/bin/env python3
"""Record the P6 evidence video from the FUXA HMI screen.

One take, four scenes, all against the live PLC:
  1. ten boxes straight through, counters climbing
  2. a LARGE box, E-Stop pressed while pusher 1 is out
  3. the two-step recovery: Reset, then Start
  4. HMI Stop, then HMI Start on the real buttons

Frames are grabbed over CDP at about 5 fps and cut together at their real
timestamps, so the video runs at wall-clock speed.

    .venv/bin/python sim/demo.py            # -> docs/demo.mp4
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import websocket

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fuxa"))
from plant import TICK_S, Box, Plant          # noqa: E402
from shot import cdp                          # noqa: E402
from tests import Plc2, Runtime, RUNNING      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/demo.mp4"
FRAMES = Path("/tmp/opencode/demo-frames")
PORT, URL = 9222, "http://localhost:1881/"
BTN_START = "HXB_00000000-0000-4000-8000-000000000010"
BTN_STOP = "HXB_00000000-0000-4000-8000-000000000011"


def click(ws, seq, lock, wid: str) -> None:
    """Press a FUXA HTML button the way the browser does: mousedown, then up."""
    js = (f"(() => {{const b=document.getElementById('B-{wid}');"
          f"const o={{bubbles:true,cancelable:true,view:window}};"
          f"b.dispatchEvent(new MouseEvent('mousedown', o));"
          f"setTimeout(()=>b.dispatchEvent(new MouseEvent('mouseup', o)),250);"
          f"return !!b;}})()")
    with lock:
        cdp(ws, seq, "Runtime.evaluate", expression=js, returnByValue=True)


def settle_until(plc: Plc2, want: int, timeout_s: float = 5.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if plc.state() == want:
            return True
        time.sleep(TICK_S)
    return False


def main() -> int:
    rt = Runtime()
    rt.cycle()
    plc = Plc2("127.0.0.1", 502)
    plc.wait_ready()
    plc.boot()
    if not plc.start():
        raise SystemExit("PLC did not reach RUNNING")

    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in FRAMES.glob("*.jpg"):
        old.unlink()

    chrome = subprocess.Popen(
        ["google-chrome-stable", "--headless=new", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}",
         "--remote-allow-origins=*", "--window-size=1340,880", URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames: list[tuple[float, bytes]] = []
    marks: list[tuple[float, str]] = []
    stop = threading.Event()
    try:
        target = None
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                target = next((t for t in tabs if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not target:
            raise SystemExit("no debug target; is chrome running?")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
        seq, lock = [0], threading.Lock()
        cdp(ws, seq, "Page.enable")
        time.sleep(10)                     # the Angular app paints, first poll lands

        def mark(label: str) -> None:
            marks.append((time.time(), label))
            print(f"{time.strftime('%H:%M:%S')}  {label}", flush=True)

        def grab() -> None:
            while not stop.is_set():
                try:
                    with lock:
                        shot = cdp(ws, seq, "Page.captureScreenshot",
                                   format="jpeg", quality=85)
                    frames.append((time.time(), base64.b64decode(shot["data"])))
                except Exception:
                    break
                time.sleep(0.2)

        thread = threading.Thread(target=grab, daemon=True)
        mark("10 box run")
        thread.start()

        # 1. ten boxes, the scenario the acceptance run uses
        boxes = [Box(id=1, size="small"), Box(id=2, size="large"),
                 Box(id=3, size="small", uncertain=True), Box(id=4, size="small"),
                 Box(id=5, size="large"), Box(id=6, size="small"),
                 Box(id=7, size="large"), Box(id=8, size="small", uncertain=True),
                 Box(id=9, size="small"), Box(id=10, size="small", uncertain=True)]
        r = plc.feed(boxes)
        mark(f"10 boxes sorted, {len(r['violations'])} valve problems")

        # 2. E-Stop while pusher 1 is out, box halfway into the large lane
        plant = Plant(boxes=[Box(id=1, size="large")], start_delay_s=0.5)
        tripped = False
        while plant.t < 30 and not tripped:
            qx = plc.read_outputs()
            if qx["P1_VALVE"]:                  # pusher 1 energized: box mid-divert
                plant.force_estop(True)
                tripped = True
            plant.step(TICK_S, qx)
            plc.write_inputs(plant.inputs())
            time.sleep(TICK_S)
        if not tripped:
            raise SystemExit("the box never reached pusher 1: no E-Stop scene")
        mark("E-Stop pressed during divert")
        for _ in range(int(4.0 / TICK_S)):        # hold it so the HMI shows ESTOP
            plant.step(TICK_S, plc.read_outputs())
            plc.write_inputs(plant.inputs())
            time.sleep(TICK_S)

        # 3. two-step recovery: Reset, then Start
        plant.force_estop(False)
        plc.pump(plant, 0.6)
        mark("recovery 1 of 2 - Reset")
        plc.pulse("PB_RESET")
        settle_until(plc, 0)
        time.sleep(1.2)
        mark("recovery 2 of 2 - Start")
        plc.pulse("PB_START")
        settle_until(plc, RUNNING)
        time.sleep(1.2)

        # 4. HMI Stop and HMI Start on the real buttons
        mark("HMI Stop")
        click(ws, seq, lock, BTN_STOP)
        time.sleep(2.5)
        hmi_stopped = plc.state() != RUNNING
        mark("HMI Start")
        click(ws, seq, lock, BTN_START)
        time.sleep(2.5)
        hmi_started = plc.state() == RUNNING
        mark("line running")
        time.sleep(2.0)
        print(f"HMI buttons: stop={hmi_stopped} start={hmi_started}", flush=True)
    finally:
        stop.set()
        time.sleep(0.4)
        chrome.terminate()
        chrome.wait(timeout=10)
        plc.close()

    if len(frames) < 20:
        raise SystemExit(f"only {len(frames)} frames captured")
    base = frames[0][0]
    listing = ["ffconcat version 1.0"]
    for i, (t, data) in enumerate(frames):
        path = FRAMES / f"f{i:05d}.jpg"
        path.write_bytes(data)
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 1.0
        listing.append(f"file '{path}'")
        listing.append(f"duration {max(0.05, nxt - t):.3f}")
    (FRAMES / "list.txt").write_text("\n".join(listing) + "\n")

    def esc(text: str) -> str:
        return text.replace("\\", "").replace("'", "").replace(":", " -")

    filters = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    for i, (t, label) in enumerate(marks):
        start = max(0.0, t - base)
        end = (marks[i + 1][0] - base) if i + 1 < len(marks) else start + 4.0
        filters.append(
            f"drawtext=text='{esc(label)}':x=18:y=18:fontsize=26:fontcolor=white:"
            f"box=1:boxcolor=black@0.65:boxborderw=10:"
            f"enable='between(t,{start:.2f},{end:.2f})'")
    filters.append("format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(FRAMES / "list.txt"), "-vf", ",".join(filters),
                    "-r", "10", "-c:v", "libx264", "-crf", "23",
                    "-movflags", "+faststart", str(OUT)], check=True)
    print(f"wrote {OUT} ({len(frames)} frames, {frames[-1][0] - base:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())