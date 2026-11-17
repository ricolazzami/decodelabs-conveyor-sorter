#!/usr/bin/env python3
"""Screenshot (and optionally dump the text of) the FUXA HMI page over CDP.

Chrome's one-shot --screenshot flag fires before the FUXA Angular app has
connected its socket and painted the first live values, so the picture shows
zeros. This keeps a real browser open for a settle time, then screenshots the
page it actually rendered.

    python3 fuxa/shot.py            # -> docs/img/fuxa.png
    python3 fuxa/shot.py --text     # also print the visible text (values check)
"""
import argparse
import base64
import json
import subprocess
import time
import urllib.request
import websocket

URL = "http://localhost:1881/"
OUT = "docs/img/fuxa.png"
PORT = 9222


def cdp(ws, seq, method, **params):
    seq[0] += 1
    ws.send(json.dumps({"id": seq[0], "method": method, "params": params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == seq[0]:
            return msg.get("result", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--settle", type=float, default=12.0)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    chrome = subprocess.Popen(
        ["google-chrome-stable", "--headless=new", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         "--window-size=1340,880", URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        seq = [0]
        cdp(ws, seq, "Page.enable")
        time.sleep(args.settle)
        if args.text:
            r = cdp(ws, seq, "Runtime.evaluate",
                    expression="document.body.innerText", returnByValue=True)
            print(r["result"]["value"])
        shot = cdp(ws, seq, "Page.captureScreenshot", format="png")
        with open(args.out, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("wrote", args.out)
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)


if __name__ == "__main__":
    main()