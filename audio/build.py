#!/usr/bin/env python3
"""Build the soundtrack for the demo video from the recording's cue sheet.

    python audio/build.py --cues /tmp/.../cues.json --out audio/demo.wav

Every machine sound is synthesised here with numpy (there is no industrial motor
or pneumatic recording in the earshot bank); the HMI click is rendered from the
earshot bank by id. The mix is then aimed at -16 LUFS integrated with true peak
at or below -1 dBTP.

Cue kinds written by sim/demo3d.py:
    ["motor", "on"|"off"]   ["puff", "p1"|"p2"]   ["thud", "small|large|reject"]
    ["estop", ""]           ["click", "start"|"stop"]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

FS = 48000
TARGET_LUFS = -16.0
TRUE_PEAK = -1.2
EARSHOT = Path("/home/ricou/projects/earshot")

rng = np.random.default_rng(20261117)


# --- small dsp kit ---------------------------------------------------------
def t_of(n: int) -> np.ndarray:
    return np.arange(n) / FS


def noise(seconds: float, colour: str = "white") -> np.ndarray:
    x = rng.standard_normal(int(seconds * FS))
    if colour == "pink":
        spec = np.fft.rfft(x)
        f = np.arange(len(spec))
        f[0] = 1
        x = np.fft.irfft(spec / np.sqrt(f), n=len(x))
        x /= max(1e-9, np.abs(x).max())
    return x


def bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Zero-phase FFT band pass; plenty for short one-shots."""
    n = len(x)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / FS)
    mask = np.ones_like(f)
    mask[f < lo] = 0.0
    mask[f > hi] = 0.0
    edge = 0.25
    for f0 in (lo, hi):
        band = (f > f0 * (1 - edge)) & (f < f0 * (1 + edge))
        ramp = np.abs(np.log(np.maximum(f[band] / f0, 1e-6)) / (edge * 0.7))
        mask[band] = np.clip(ramp, 0, 1)
    return np.fft.irfft(spec * mask, n=n)


def lowpass(x: np.ndarray, fc: float) -> np.ndarray:
    return bandpass(x, 20.0, fc)


def env_decay(n: int, tau: float, attack: float = 0.0008) -> np.ndarray:
    t = t_of(n)
    a = np.clip(t / max(attack, 1e-6), 0, 1)
    return a * np.exp(-t / tau)


def place(dst: np.ndarray, x: np.ndarray, t: float, gain: float = 1.0, pan: float = 0.0):
    i = int(t * FS)
    if i >= dst.shape[1] or i + len(x) <= 0:
        return
    j = min(dst.shape[1], i + len(x))
    seg = x[: j - i] * gain
    left = np.sqrt(0.5 * (1 - pan))
    right = np.sqrt(0.5 * (1 + pan))
    dst[0, i:j] += seg * left * 1.41
    dst[1, i:j] += seg * right * 1.41


# --- the machine sounds ----------------------------------------------------
def motor_bed(seconds: float) -> np.ndarray:
    """Three-phase induction motor on a steel frame: 50 Hz mains hum with its
    harmonics, a 12 Hz beat from the belt splice, gear whine and bearing noise."""
    n = int(seconds * FS)
    t = t_of(n)
    x = np.zeros(n)
    for k, amp in ((1, 1.00), (2, 0.55), (3, 0.28), (4, 0.14), (6, 0.07), (8, 0.04)):
        x += amp * np.sin(2 * np.pi * 50 * k * t + k * 0.7)
    x *= 1.0 + 0.10 * np.sin(2 * np.pi * 12.0 * t)            # splice beat
    whine = 0.10 * np.sin(2 * np.pi * 615 * t) + 0.05 * np.sin(2 * np.pi * 930 * t)
    hiss = lowpass(noise(seconds, "pink"), 1200.0) * 0.10
    rumble = bandpass(noise(seconds), 30.0, 220.0) * 0.16
    y = x * 0.42 + whine + hiss + rumble
    xmax = max(1e-9, np.abs(y).max())
    return y / xmax


def room_tone(seconds: float) -> np.ndarray:
    """The workshop the machine stands in: HVAC rumble, fan whine, air. Without
    this the seconds after an E-Stop would be digital silence, not a stopped
    machine in a room."""
    n = int(seconds * FS)
    t = t_of(n)
    hum = (0.6 * np.sin(2 * np.pi * 100 * t) + 0.25 * np.sin(2 * np.pi * 200 * t + 0.4)
           + 0.12 * np.sin(2 * np.pi * 300 * t + 0.9))
    fan = 0.10 * np.sin(2 * np.pi * 1180 * t) + 0.06 * np.sin(2 * np.pi * 47 * t)
    air = lowpass(noise(seconds, "pink"), 3000.0) * 0.5
    y = hum * 0.5 + fan + air
    return y / max(1e-9, np.abs(y).max())


def pneumatic_puff() -> np.ndarray:
    """A 5/2 valve firing: air release, hard leading edge, body resonance."""
    n = int(0.22 * FS)
    air = bandpass(noise(0.22), 500.0, 6500.0) * env_decay(n, 0.045, 0.002)
    body = np.sin(2 * np.pi * 240 * t_of(n)) * env_decay(n, 0.03)
    seat = bandpass(noise(0.22), 1500.0, 9000.0) * env_decay(n, 0.006, 0.0004) * 0.8
    y = air * 0.75 + body * 0.30 + seat
    return y / max(1e-9, np.abs(y).max())


def carton_thud() -> np.ndarray:
    """A full carton landing on a steel deck: soft body, no ring."""
    n = int(0.30 * FS)
    t = t_of(n)
    thump = np.sin(2 * np.pi * 74 * t) * env_decay(n, 0.055, 0.001)
    knock = bandpass(noise(0.30), 120.0, 900.0) * env_decay(n, 0.020, 0.0006)
    slap = bandpass(noise(0.30), 1200.0, 5000.0) * env_decay(n, 0.010, 0.0004) * 0.45
    y = thump * 0.85 + knock * 0.55 + slap
    return y / max(1e-9, np.abs(y).max())


def estop_clunk() -> np.ndarray:
    """The mushroom hitting its detent: a hard click, a heavy plate thump and a
    short ring in the panel."""
    n = int(0.9 * FS)
    t = t_of(n)
    click = bandpass(noise(0.9), 2000.0, 12000.0) * env_decay(n, 0.004, 0.0002)
    thump = np.sin(2 * np.pi * 58 * t) * env_decay(n, 0.13, 0.001) * 1.0
    plate = np.sin(2 * np.pi * 168 * t) * env_decay(n, 0.06, 0.001) * 0.45
    ring = sum(a * np.sin(2 * np.pi * f * t) * env_decay(n, d, 0.002)
               for f, d, a in ((431, 0.26, 0.30), (789, 0.18, 0.20), (1312, 0.11, 0.12)))
    y = click * 0.9 + thump + plate + ring
    return y / max(1e-9, np.abs(y).max())


def hmi_click() -> np.ndarray | None:
    """Rendered from the earshot bank (produced with earshot sfx render): a tap on
    a touch panel, not a mouse click, so it carries against the room tone."""
    out = Path(tempfile.gettempdir()) / "earshot-hmi-click.wav"
    try:
        subprocess.run([sys.executable, "-m", "earshot", "sfx", "render", "knock/plastic",
                        "-o", str(out)], cwd=str(EARSHOT), check=True,
                       capture_output=True, timeout=180)
    except Exception as exc:                                   # silent fallback
        print(f"  (earshot click not available: {exc})", file=sys.stderr)
        return None
    with wave.open(str(out)) as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:                                     # the bank renders 24-bit
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.uint32)
        v = (b[:, 0] << 8) | (b[:, 1] << 16) | (b[:, 2] << 24)
        data = v.astype(np.int32).astype(np.float64) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    if channels == 2:
        data = data.reshape(-1, 2).mean(axis=1)
    x = data
    return x / max(1e-9, np.abs(x).max())


# --- loudness --------------------------------------------------------------
def measure(path: Path) -> tuple[float, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) as ffmpeg measures them."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    text = r.stderr
    integrated = re.findall(r"I:\s*(-?\d+\.\d+)\s*LUFS", text)
    peaks = re.findall(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", text)
    if not integrated or not peaks:
        raise SystemExit("could not measure loudness (is ffmpeg installed?)")
    return float(integrated[-1]), float(peaks[-1])


def loudnorm(src: Path, dst: Path):
    """ffmpeg's own two-pass loudnorm: linear gain first, limiter only if needed."""
    probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src),
                            "-af", f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11:print_format=json",
                            "-f", "null", "-"], capture_output=True, text=True)
    blob = probe.stderr[probe.stderr.rfind("{"):probe.stderr.rfind("}") + 1]
    m = json.loads(blob)
    chain = (f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA=11"
             f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
             f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
             f":offset={m['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", chain,
                    "-ar", str(FS), "-ac", "2", "-c:a", "pcm_s16le", str(dst)], check=True)


def write_wav(path: Path, x: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(x, -1.0, 1.0).T
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((data * 32767.0).astype(np.int16).tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sheet = json.loads(Path(args.cues).read_text())
    duration = float(sheet["duration"]) + 0.6
    cues = sorted((float(c[0]), c[1], c[2] if len(c) > 2 else "") for c in sheet["cues"])
    mix = np.zeros((2, int(duration * FS)))
    print(f"  {len(cues)} cues over {duration:.1f} s")

    # 1. motor bed: one segment per on..off pair
    on_at = [t for t, kind, what in cues if kind == "motor" and what == "on"]
    off_at = [t for t, kind, what in cues if kind == "motor" and what == "off"]
    if not on_at:
        on_at, off_at = [0.0], [duration]
    for i, start in enumerate(on_at):
        end = next((t for t in off_at if t > start), duration)
        seg = motor_bed(max(0.2, end - start + 0.15))
        ramp = int(0.25 * FS)
        seg[:ramp] *= np.linspace(0.0, 1.0, ramp)
        place(mix, seg, start, gain=0.30)

    place(mix, room_tone(duration), 0.0, gain=0.035)          # room under everything
    puff = pneumatic_puff()
    thud = carton_thud()
    clunk = estop_clunk()
    click = hmi_click()

    for t, kind, what in cues:
        if kind == "puff":
            place(mix, puff, t, gain=0.55, pan=-0.45 if what == "p1" else 0.45)
        elif kind == "thud":
            pan = {"large": -0.55, "reject": 0.55}.get(what, 0.0)
            place(mix, thud, t, gain=0.75, pan=pan)
        elif kind == "estop":
            place(mix, clunk, t, gain=0.95)
        elif kind == "click" and click is not None:
            place(mix, click, t, gain=0.85, pan=0.35)

    # 2. room: a short plate reverb so the line is not in a vacuum
    ir_len = int(0.45 * FS)
    ir = lowpass(noise(0.45), 4500.0) * np.exp(-t_of(ir_len) / 0.10)
    ir /= max(1e-9, np.abs(ir).max())
    for ch in range(2):
        wet = np.convolve(mix[ch], ir, mode="full")[: mix.shape[1]]
        mix[ch] += 0.10 * wet / max(1e-9, np.abs(wet).max()) * 0.5

    # 3. fade in/out, then aim the loudness at -16 LUFS / -1 dBTP
    fade = int(0.35 * FS)
    mix[:, :fade] *= np.linspace(0.0, 1.0, fade)
    mix[:, -fade:] *= np.linspace(1.0, 0.0, fade)

    out = Path(args.out)
    raw = out.with_suffix(".raw.wav")
    write_wav(raw, mix)
    loudnorm(raw, out)                                    # two-pass, linear gain
    raw.unlink(missing_ok=True)
    lufs, peak = measure(out)
    print(f"  {out}  {lufs:.1f} LUFS  true peak {peak:.1f} dBTP  {duration:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())