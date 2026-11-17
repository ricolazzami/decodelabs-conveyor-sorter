#!/usr/bin/env python3
"""Serve the 3D view, run the acceptance scenario against the live PLC, record.

    .venv/bin/python sim/demo3d.py             # record docs/demo.mp4
    .venv/bin/python sim/demo3d.py --live      # just serve http://localhost:8099

The page polls /state at about 16 Hz. Box positions and lane decisions are the
plant simulator's truth (there are no physical sensors); motor, valves, state,
counters and fault are the PLC's own outputs and words.

How the video is made
---------------------
Three things run at once and all three are stamped with time.time():

  * the 3D page records its own canvas with MediaRecorder, 10 frames a second
    pushed on a timer, posted back to /rec/scene (CDP screenshots of a 1080p
    page cost 160 ms each here, so they are only used for the HMI),
  * a second headless Chrome shows the real FUXA screen and is screenshotted
    twice a second,
  * the plant and the PLC run the scenario and write their sound cues.

ffmpeg then lays the FUXA screenshot over the empty picture-in-picture area,
draws the phase captions, and muxes the soundtrack audio/build.py made.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import websocket

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fuxa"))
from plant import TICK_S, Box, Plant                 # noqa: E402
from shot import cdp                                # noqa: E402
from tests import Plc2, Runtime                     # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VIZ = ROOT / "viz"
OUT = ROOT / "docs/demo.mp4"
WORK = Path("/tmp/opencode/demo3d")
FRAMES = WORK / "frames"
PORT, CDP_PORT, HMI_CDP_PORT = 8099, 9223, 9224
HMI_URL = "http://localhost:1881/"
BTN_START = "HXB_00000000-0000-4000-8000-000000000010"
BTN_STOP = "HXB_00000000-0000-4000-8000-000000000011"
EARSHOT_PY = Path(os.environ.get("EARSHOT_PY", "/home/ricou/projects/earshot/.venv/bin/python"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# the PiP well in viz/style.css: right 22, bottom 22, 560x368, 3px border
PIP = dict(x=1338, y=690, w=560, h=368, border=3)
HMI_VIEW = (1340, 880)              # the FUXA view the HMI was built for

CHROME_FLAGS = ["--headless=new", "--no-sandbox", "--enable-gpu",
                "--use-angle=vulkan", "--ignore-gpu-blocklist", "--hide-scrollbars",
                "--remote-allow-origins=*"]

SNAP: dict = {"caption": "connecting to the plant...", "boxes": []}
EVENTS: list[tuple[float, str, str]] = []
CAPTIONS: list[tuple[float, str]] = []
PLANT: dict = {}
SCENE_DONE = threading.Event()


def publish(plc: Plc2, plant: Plant | None = None, caption: str | None = None,
            zoom: float | None = None) -> None:
    w = plc.read_words()
    q = plc.read_outputs()
    if plant is None:
        boxes, s1, s2 = [], False, False
    else:
        boxes = [{"id": b.id, "x": round(b.x, 1), "size": b.size,
                  "uncertain": b.uncertain, "lane": b.lane}
                 for b in plant.boxes if b.id <= plant.spawn_idx]
        inp = plant.inputs()
        s1, s2 = inp["S1_BOX"], inp["S2_BOX"]
    SNAP.update({"boxes": boxes, "s1": bool(s1), "s2": bool(s2),
                 "motor": q["MOTOR_RUN"], "p1": q["P1_VALVE"], "p2": q["P2_VALVE"],
                 "state": w["state"], "fault": w["fault_code"],
                 "counters": [w["count_small"], w["count_large"], w["count_reject"]]})
    if caption is not None and caption != SNAP.get("caption"):
        SNAP["caption"] = caption
        CAPTIONS.append((time.time(), caption))
    if zoom is not None:
        SNAP["zoom"] = zoom


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _ok(self, body: bytes = b"{}"):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/state":
            self._ok(json.dumps(SNAP).encode())
            return
        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path.startswith("/rec/open"):
            WORK.mkdir(parents=True, exist_ok=True)
            (WORK / "scene.webm").write_bytes(b"")
        elif self.path.startswith("/rec/chunk"):
            with open(WORK / "scene.webm", "ab") as fh:
                fh.write(body)
        elif self.path.startswith("/rec/done"):
            SCENE_DONE.set()
        self._ok()

    def log_message(self, *args):
        pass


def serve() -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=str(VIZ)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class CDP:
    """One websocket with a reader thread: replies by id, screenshots on demand."""

    def __init__(self, port: int, view: tuple[int, int] = (1920, 1080)):
        target = None
        for _ in range(80):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                target = next((t for t in tabs if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not target:
            raise SystemExit(f"no debug target on port {port}; is chrome running?")
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60)
        self.seq = 0
        self.seq_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.pending: dict[int, list] = {}
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=view[0], height=view[1],
                  deviceScaleFactor=1, mobile=False)

    def _send(self, msg: dict):
        with self.send_lock:
            self.ws.send(json.dumps(msg))

    def _read(self):
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                break
            if "id" in msg:
                slot = self.pending.pop(msg["id"], None)
                if slot:
                    slot[1] = msg
                    slot[0].set()

    def call(self, method: str, **params) -> dict:
        with self.seq_lock:
            self.seq += 1
            sid = self.seq
        slot = [threading.Event(), None]
        self.pending[sid] = slot
        self._send({"id": sid, "method": method, "params": params})
        if not slot[0].wait(60):
            raise TimeoutError(method)
        return (slot[1] or {}).get("result", {})

    def eval(self, expression: str):
        return self.call("Runtime.evaluate", expression=expression,
                         returnByValue=True).get("result", {}).get("value")

    def shot(self, path: Path, quality: int = 88) -> None:
        data = self.call("Page.captureScreenshot", format="jpeg", quality=quality)
        path.write_bytes(base64.b64decode(data["data"]))

    def click(self, wid: str) -> bool:
        """A real mousedown/mouseup on a FUXA button (the HMI is a top-level page
        here, so no coordinate maths and no isolated world are needed)."""
        js = (f"(() => {{const b=document.getElementById('B-{wid}');"
              f"if(!b) return false;"
              f"const o={{bubbles:true,cancelable:true,view:window}};"
              f"b.dispatchEvent(new MouseEvent('mousedown', o));"
              f"setTimeout(()=>b.dispatchEvent(new MouseEvent('mouseup', o)),250);"
              f"return true;}})()")
        return bool(self.eval(js))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def hmi_grabber(hmi: CDP, result: list, stop: threading.Event, folder: Path) -> None:
    """Screenshot the FUXA screen twice a second: it only changes when a value does."""
    i = 0
    while not stop.is_set():
        try:
            path = folder / f"h{i:05d}.jpg"
            hmi.shot(path)
            result.append((time.time(), path))
            i += 1
        except Exception as exc:
            print(f"  HMI frame failed: {exc!r}")
            break
        time.sleep(0.5 - 0.16)


def outputs(plc: Plc2) -> dict:
    q = plc.read_outputs()
    return {"motor": q["MOTOR_RUN"], "p1": q["P1_VALVE"], "p2": q["P2_VALVE"]}


def tick(plc: Plc2, plant: Plant, caption: str, zoom: float | None = None) -> None:
    """One plant step, and the sound cues that come with it."""
    prev = PLANT.get("last", {})
    now = outputs(plc)
    for key in ("p1", "p2"):
        if now[key] and not prev.get(key):
            EVENTS.append((time.time(), "puff", key))
    if now["motor"] != prev.get("motor"):
        EVENTS.append((time.time(), "motor", "on" if now["motor"] else "off"))
    for b in plant.boxes:
        if b.id <= plant.spawn_idx and b.lane and b.id not in PLANT.setdefault("lanes", {}):
            PLANT["lanes"][b.id] = b.lane
            EVENTS.append((time.time(), "thud", b.lane))
    PLANT["last"] = now
    plant.step(TICK_S, plc.read_outputs())
    plc.write_inputs(plant.inputs())
    publish(plc, plant, caption, zoom)
    time.sleep(TICK_S)


def hold(plc: Plc2, plant: Plant, caption: str, seconds: float,
         zoom: float | None = None) -> None:
    publish(plc, plant, caption, zoom)
    end = time.time() + seconds
    while time.time() < end:
        tick(plc, plant, caption, zoom)


def run_boxes(plc: Plc2, boxes: list[Box], caption: str, timeout_s: float = 60.0,
              zoom: float | None = None) -> Plant:
    plant = Plant(boxes=boxes, pitch_mm=600.0, start_delay_s=1.0)
    PLANT["lanes"] = {}
    while plant.t < timeout_s and not plant.done():
        tick(plc, plant, caption, zoom)
    while plant.t < timeout_s + 3.0:
        q = plc.read_outputs()
        if not (q["P1_VALVE"] or q["P2_VALVE"]):
            break
        tick(plc, plant, caption, zoom)
    return plant


def assemble(t0: float, hmi_frames: list, audio: Path) -> None:
    """Lay the FUXA frames over the PiP well, draw the captions, mux the sound."""
    folder = FRAMES
    listing = ["ffconcat version 1.0"]
    first_t = hmi_frames[0][0] - t0
    listing.append(f"file '{(folder / 'h00000.jpg').as_posix()}'")
    listing.append(f"duration {max(0.04, 0.5 if first_t <= 0 else first_t):.3f}")
    for i, (t, path) in enumerate(hmi_frames):
        nxt = hmi_frames[i + 1][0] if i + 1 < len(hmi_frames) else t + 0.5
        listing.append(f"file '{path.as_posix()}'")
        listing.append(f"duration {max(0.04, nxt - t):.3f}")
    (WORK / "hmi-list.txt").write_text("\n".join(listing) + "\n")

    caption_files = []
    for i, (t, text) in enumerate(CAPTIONS):
        p = WORK / f"cap{i:02d}.txt"
        p.write_text(text)
        caption_files.append((t - t0 if i else 0.0,
                              CAPTIONS[i + 1][0] - t0 if i + 1 < len(CAPTIONS) else 1e3,
                              p))

    inner_w, inner_h = PIP["w"] - 2 * PIP["border"], PIP["h"] - 2 * PIP["border"]
    filters = [
        f"[1:v]scale={inner_w}:{inner_h},setsar=1,"
        f"pad={PIP['w']}:{PIP['h']}:{PIP['border']}:{PIP['border']}:color=0x30363d[hmi]",
        f"[0:v][hmi]overlay={PIP['x']}:{PIP['y']}:eof_action=pass[v0]",
    ]
    for i, (start, end, path) in enumerate(caption_files):
        filters.append(
            f"[v{i}]drawtext=fontfile={FONT}:textfile={path}:fontsize=38:fontcolor=white"
            f":box=1:boxcolor=0x040a12@0.80:boxborderw=16:x=34:y=30"
            f":enable='between(t,{start:.2f},{end:.2f})'[v{i + 1}]")
    graph = ";".join(filters)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(WORK / "scene.webm"),
        "-f", "concat", "-safe", "0", "-i", str(WORK / "hmi-list.txt"),
        "-i", str(audio),
        "-filter_complex", graph,
        "-map", f"[v{len(caption_files)}]", "-map", "2:a",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        str(OUT)], check=True)


def record(rt: Runtime, plc: Plc2) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    FRAMES.mkdir(parents=True, exist_ok=True)
    for old in list(FRAMES.glob("*.jpg")) + list(FRAMES.glob("*.png")):
        old.unlink()
    SCENE_DONE.clear()

    viz_chrome = subprocess.Popen(["google-chrome-stable", *CHROME_FLAGS,
                                   f"--remote-debugging-port={CDP_PORT}",
                                   "--window-size=1920,1080", f"http://localhost:{PORT}/?rec=1"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hmi_chrome = subprocess.Popen(["google-chrome-stable", *CHROME_FLAGS,
                                   f"--remote-debugging-port={HMI_CDP_PORT}",
                                   f"--window-size={HMI_VIEW[0]},{HMI_VIEW[1] + 240}", HMI_URL],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        viz = CDP(CDP_PORT, (1920, 1080))
        for _ in range(80):
            if viz.eval("!!document.querySelector('canvas')"):
                break
            time.sleep(0.25)
        hmi = CDP(HMI_CDP_PORT, HMI_VIEW)
        time.sleep(10)                      # fonts, first HMI poll, belt paint
        hmi.eval("document.body.style.overflow='hidden'")

        rt.cycle()
        plc.wait_ready()
        plc.boot()
        if not plc.start():
            raise SystemExit("PLC did not reach RUNNING")

        PLANT["lanes"] = {}
        publish(plc, Plant(boxes=[], start_delay_s=0.0), "Conveyor box sorter — sort by size", 4.35)
        CAPTIONS.clear()
        EVENTS.clear()
        hmi_frames: list = []
        stop_hmi = threading.Event()
        t0 = time.time()
        viz.eval("window.__recStart()")
        threading.Thread(target=hmi_grabber, args=(hmi, hmi_frames, stop_hmi, FRAMES),
                         daemon=True).start()
        print(f"recording from t0={t0:.3f}")

        empty = Plant(boxes=[], start_delay_s=0.0)
        hold(plc, empty, "Conveyor box sorter — sort by size", 2.2)
        hold(plc, empty, "conveyor running at 300 mm/s — no boxes yet", 1.4)

        boxes = [Box(id=1, size="small"), Box(id=2, size="large"),
                 Box(id=3, size="small", uncertain=True), Box(id=4, size="small"),
                 Box(id=5, size="large"), Box(id=6, size="small"),
                 Box(id=7, size="large"), Box(id=8, size="small", uncertain=True),
                 Box(id=9, size="small"), Box(id=10, size="small", uncertain=True)]
        plant = run_boxes(plc, boxes, "10 boxes — tall left, small straight, uncertain rejected")
        w = plc.read_words()
        hold(plc, plant, f"done — {w['count_small']} small · {w['count_large']} large · "
                         f"{w['count_reject']} rejected", 1.5)

        # --- E-Stop with a box in front of pusher 1 -----------------------
        plant.boxes.append(Box(id=11, size="large"))
        while plant.t < 80:
            if plc.read_outputs()["P1_VALVE"]:
                plant.force_estop(True)
                EVENTS.append((time.time(), "estop", ""))
                break
            tick(plc, plant, "E-Stop — a box waits at pusher 1", 3.0)
        else:
            raise SystemExit("the box never reached pusher 1: no E-Stop scene")
        hold(plc, plant, "E-Stop pressed — motor and both valves dead at once", 2.8, 2.9)

        # --- two-step recovery -------------------------------------------
        publish(plc, plant, "recovery 1 of 2 — Reset clears the fault", 3.4)
        time.sleep(1.0)
        plant.force_estop(False)
        plc.pulse("PB_RESET")
        hold(plc, plant, "recovery 1 of 2 — Reset clears the fault, line stays stopped", 1.8, 3.4)
        publish(plc, plant, "recovery 2 of 2 — Start re-runs the line", 3.8)
        time.sleep(1.0)
        plc.pulse("PB_START")
        hold(plc, plant, "recovery 2 of 2 — Start re-runs the line", 1.4, 3.8)

        # --- the line is alive again -------------------------------------
        # The box in front of pusher 1 lost its record when the fault cleared
        # the tracking, so station 2 rejects it: the design rule at work.
        plant.boxes.append(Box(id=12, size="small"))
        while plant.t < 140 and not plant.boxes[-1].lane:
            tick(plc, plant, "line running again — the box with no record goes to REJECT", 4.0)
        w = plc.read_words()
        hold(plc, plant, f"counters kept across the fault — {w['count_small']} small · "
                         f"{w['count_large']} large · {w['count_reject']} rejected", 1.5, 4.0)

        # --- the HMI can stop and start the line, real clicks -------------
        publish(plc, plant, "HMI Stop — the screen may stop the line", 4.2)
        EVENTS.append((time.time(), "click", "stop"))
        hmi.click(BTN_STOP)
        time.sleep(2.2)
        publish(plc, plant, "HMI Start — and only the screen may start it", 4.2)
        EVENTS.append((time.time(), "click", "start"))
        hmi.click(BTN_START)
        time.sleep(2.2)
        publish(plc, plant, "safety chain armed, counters kept — end of test", 4.35)
        time.sleep(1.8)

        stop_hmi.set()
        time.sleep(0.3)
        viz.eval("window.__recStop()")
        for _ in range(200):
            if SCENE_DONE.is_set() and (WORK / "scene.webm").exists():
                break
            time.sleep(0.1)
        end = time.time()
        hmi.close()
        viz.close()
    finally:
        for p in (viz_chrome, hmi_chrome):
            p.terminate()
            p.wait(timeout=20)

    if not (WORK / "scene.webm").exists():
        raise SystemExit("the page did not deliver a recording")
    duration = end - t0
    cues = [(round(t - t0, 3), kind, what) for t, kind, what in EVENTS if t >= t0 - 0.2]
    (WORK / "cues.json").write_text(json.dumps({"duration": round(duration, 3),
                                               "cues": cues}, indent=1))
    print(f"{duration:.1f} s, {len(hmi_frames)} HMI frames, {len(cues)} sound cues, "
          f"{len(CAPTIONS)} captions")

    audio = ROOT / "audio"
    subprocess.run([str(EARSHOT_PY), str(audio / "build.py"),
                    "--cues", str(WORK / "cues.json"), "--out", str(audio / "demo.wav")],
                   check=True, cwd=ROOT)
    assemble(t0, hmi_frames, audio / "demo.wav")
    print(f"wrote {OUT}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="only serve the page")
    args = ap.parse_args()
    httpd = serve()
    print(f"3D view: http://localhost:{PORT}/")
    rt = Runtime()
    plc = Plc2("127.0.0.1", 502)
    plc.wait_ready()
    if args.live:
        plc.boot()
        plc.start()
        boxes = [Box(id=1, size="small"), Box(id=2, size="large"),
                 Box(id=3, size="small", uncertain=True), Box(id=4, size="small"),
                 Box(id=5, size="large"), Box(id=6, size="small")]
        try:
            run_boxes(plc, boxes, "live run — six boxes")
            publish(plc, caption="run finished — start sim/demo3d.py --live again")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            httpd.shutdown()
            plc.close()
            return 0
    try:
        record(rt, plc)
    finally:
        plc.close()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())