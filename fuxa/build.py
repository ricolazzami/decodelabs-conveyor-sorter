#!/usr/bin/env python3
"""Build the FUXA HMI project for the conveyor sorter and (optionally) apply it.

    python3 fuxa/build.py            # write fuxa/project.fuxap
    python3 fuxa/build.py --apply    # write it, then POST to the running FUXA

The FUXA project file is plain JSON. Keeping the generator (not just the JSON)
means the Modbus tag map below is the single place to change and the file can be
rebuilt. See docs/ARCHITECTURE.md §9 for the same map.

Modbus map (OpenPLC Runtime v4, Modbus TCP, unit 1):
  coils  0..5    MOTOR_RUN, P1_VALVE, P2_VALVE, LAMP_RUN, LAMP_FAULT, LAMP_REJ
  coils  80,81   HMI_START, HMI_STOP      (write-only from the HMI)
  HR 0..4        ST_WORD, CNT_SMALL, CNT_LARGE, CNT_REJECT, FAULT_CODE

FUXA tag addresses are 1-based inside a memory area:
  coil n -> address "n+1", memaddress "000000"
  HR   n -> address "n+1", memaddress "400000"
"""
import json
import sys
import urllib.request
from pathlib import Path

DEVICE = "PLC"
DEVICE_HOST = "plc"          # docker compose service name; FUXA is on the same network
FUXA = "http://localhost:1881"
OUT = Path(__file__).with_name("project.fuxap")

COILS = {                    # tag -> coil index
    "MOTOR_RUN": 0, "P1_VALVE": 1, "P2_VALVE": 2,
    "LAMP_RUN": 3, "LAMP_FAULT": 4, "LAMP_REJ": 5,
    "HMI_START": 80, "HMI_STOP": 81,
}
DISPLAY = {"HMI_START": "START", "HMI_STOP": "STOP"}   # FUXA prints the tag name on the button

WORDS = {                    # tag -> holding register index
    "ST_WORD": 0, "CNT_SMALL": 1, "CNT_LARGE": 2, "CNT_REJECT": 3, "FAULT_CODE": 4,
}

STATE_TEXT = ["IDLE", "READY", "RUNNING", "(reserved)", "P1 DIVERT", "P1 RETURN",
              "P2 DIVERT", "P2 RETURN", "STOPPING", "FAULT", "ESTOP"]
FAULT_TEXT = ["none", "E-Stop pressed", "P1 extend timeout", "P1 return timeout",
              "P2 extend timeout", "P2 return timeout", "motor overload",
              "box stuck between stations", "no box in pusher zone"]

# svg-ext-motor is a legacy shape the 1.3.6 client no longer knows, so the
# conveyor motor is shown with the semaphore lamp it does know.
LAMPS = [("GSE_00000000-0000-4000-8000-000000000001", "MOTOR_RUN", "conveyor motor", "#2e9e44", 130),
         ("GSE_00000000-0000-4000-8000-000000000002", "LAMP_RUN", "RUN", "#2e9e44", 290),
         ("GSE_00000000-0000-4000-8000-000000000003", "LAMP_FAULT", "FAULT", "#d32f2f", 380),
         ("GSE_00000000-0000-4000-8000-000000000004", "LAMP_REJ", "REJECT", "#f9a825", 470)]
STATE_ID = "HXS_00000000-0000-4000-8000-000000000020"
FAULT_ID = "HXS_00000000-0000-4000-8000-000000000021"
COUNTERS = [("VAL_00000000-0000-4000-8000-000000000030", "CNT_SMALL", "SMALL (lane A)", 110),
            ("VAL_00000000-0000-4000-8000-000000000031", "CNT_LARGE", "LARGE (lane B)", 290),
            ("VAL_00000000-0000-4000-8000-000000000032", "CNT_REJECT", "REJECT (lane C)", 460)]
BTN_START = "HXB_00000000-0000-4000-8000-000000000010"
BTN_STOP = "HXB_00000000-0000-4000-8000-000000000011"


def tags():
    """Tag ids carry the device prefix.

    The web client stores received values under the id the device sends, and a
    widget binds by `variableId` ("<device>^~^<tag>"), so the tag id must be
    the same full string or nothing ever matches (the view renders, the values
    arrive, and every field stays at its placeholder).
    """
    out = {}
    for name, n in COILS.items():
        full = f"{DEVICE}^~^{name}"
        out[full] = {"id": full, "name": DISPLAY.get(name, name), "type": "Bool",
                     "address": str(n + 1), "memaddress": "000000"}
    for name, n in WORDS.items():
        full = f"{DEVICE}^~^{name}"
        out[full] = {"id": full, "name": name, "type": "Int16",
                     "address": str(n + 1), "memaddress": "400000"}
    return out


def var(name):
    """The three fields every FUXA widget uses to bind a device tag."""
    return {"variable": name, "variableId": f"{DEVICE}^~^{name}", "variableSrc": DEVICE}


# FUXA's view renderer keys widgets by their LabelTag, not by the SVG type attribute.
LABEL = {"svg-ext-value": "Value", "svg-ext-gauge_semaphore": "HtmlSemaphore",
         "svg-ext-html_select": "HtmlSelect", "svg-ext-html_button": "HtmlButton"}


def widget(wid, wtype, name, extra):
    prop = {"events": [], **var(name), "alarmId": "", "alarmSrc": "",
            "alarm": "", "alarmColor": ""}
    prop.update(extra)
    return {"id": wid, "type": wtype, "name": name, "property": prop,
            "label": LABEL.get(wtype, "")}


def steps(pairs):
    return [{"type": "step", "min": str(lo), "max": str(hi), "text": txt}
            for lo, hi, txt in pairs]


def state_steps():
    return steps([(i, i, t) for i, t in enumerate(STATE_TEXT)])


def fault_steps():
    return steps([(i, i, t) for i, t in enumerate(FAULT_TEXT)])


def lamp_ranges(on_colour):
    return [{"type": "range", "min": "0", "max": "0", "color": "#bdbdbd"},
            {"type": "range", "min": "1", "max": "1", "color": on_colour}]


def svg():
    """The view: plain SVG for text/frames + one <g> scaffold per widget.

    FUXA hydrates a widget by the g's id + type, then writes the live value into
    the child shape (ellipse fill, <text> content, <SELECT> option). Text around
    the widgets is ordinary SVG and needs no item entry.
    """
    head = 'font-family="sans-serif" font-size="15" fill="#0b3d91" font-weight="bold"'
    body = 'font-family="sans-serif" font-size="13" fill="#333333"'
    title = 'font-family="sans-serif" font-size="24" fill="#12181f" font-weight="bold"'
    p = []

    p.append('<rect x="0" y="0" width="1280" height="760" fill="#f7f9fb"/>')
    p.append(f'<text x="40" y="46" {title}>Conveyor box sorter — HMI</text>')
    p.append(f'<text x="40" y="72" {body}>OpenPLC Runtime v4 over Modbus TCP · sort by size · '
             'straight = SMALL, pusher 1 = LARGE, pusher 2 = REJECT</text>')

    # --- line indicators -------------------------------------------------
    p.append('<rect x="40" y="100" width="520" height="180" rx="8" fill="#ffffff" stroke="#cfd8e3"/>')
    p.append(f'<text x="60" y="130" {head}>Line</text>')
    for sid, tag, label, colour, cx in LAMPS:
        p.append(f'<g id="{sid}" type="svg-ext-gauge_semaphore" fill="#000000" '
                 'font-size="14" stroke="#000000" font-family="sans-serif" text-anchor="middle">'
                 f'<ellipse cx="{cx}" cy="195" rx="22" ry="22" fill="#bdbdbd" stroke="#000000"/>'
                 '</g>')
        p.append(f'<text x="{cx}" y="252" {body} text-anchor="middle">{label}</text>')

    # --- status panels ---------------------------------------------------
    p.append('<rect x="600" y="100" width="300" height="180" rx="8" fill="#ffffff" stroke="#cfd8e3"/>')
    p.append(f'<text x="620" y="130" {head}>Status</text>')
    p.append(f'<text x="620" y="166" {body}>state</text>')
    p.append(f'<text x="620" y="216" {body}>fault</text>')

    # --- counters --------------------------------------------------------
    p.append('<rect x="40" y="300" width="520" height="150" rx="8" fill="#ffffff" stroke="#cfd8e3"/>')
    p.append(f'<text x="60" y="330" {head}>Counters</text>')
    for vid, tag, label, x in COUNTERS:
        p.append(f'<g id="{vid}" type="svg-ext-value" fill="#000000" stroke-width="0" '
                 'font-family="sans-serif" text-anchor="middle" font-size="28">'
                 f'<text x="{x}" y="402" text-anchor="middle" font-family="sans-serif" '
                 'font-size="34" fill="#12181f">0</text></g>')
        p.append(f'<text x="{x}" y="432" {body} text-anchor="middle">{label}</text>')

    # --- controls --------------------------------------------------------
    p.append('<rect x="40" y="480" width="520" height="170" rx="8" fill="#ffffff" stroke="#cfd8e3"/>')
    p.append(f'<text x="60" y="510" {head}>Control (web HMI)</text>')

    # --- notes -----------------------------------------------------------
    p.append(f'<text x="600" y="332" {head}>What the HMI may and may not do</text>')
    for i, line in enumerate([
        "Start and Stop are allowed from this screen (momentary, held for the click).",
        "There is no Reset control here, by design (SR-08): a safety latch is only",
        "cleared by the physical Reset button on the panel, then a separate Start.",
        "The E-Stop is hardwired in the motor contactor circuit — the HMI cannot",
        "defeat it, and losing this screen never stops or restarts the line (TC-29).",
    ]):
        p.append(f'<text x="600" y="{364 + i * 24}" {body}>{line}</text>')
    return p


def build():
    parts = svg()

    def add_button(wid, label, x, colour):
        # FUXA finds the clickable element by its id prefix (B-HXB_...), so the
        # BUTTON and its foreignObject must carry one.
        uid = wid.split("_", 1)[1]
        parts.append(
            f'<g id="{wid}" type="svg-ext-html_button" fill="#FFFFFF" font-size="16" '
            'font-family="sans-serif" text-anchor="middle" stroke="#000000">'
            f'<rect x="{x}" y="545" width="150" height="56" rx="6" fill="{colour}" stroke="none"/>'
            f'<foreignObject id="H-HXB_{uid}" x="{x}" y="545" width="150" height="56">'
            f'<BUTTON id="B-HXB_{uid}" style="width:100%;height:100%;background-color:{colour};'
            f'color:#ffffff;" class="md-btn md-btn-raised">{label}</BUTTON></foreignObject></g>')

    add_button(BTN_START, "START", 90, "#2e9e44")
    add_button(BTN_STOP, "STOP", 270, "#c62828")

    def add_select(wid, x, y, w, h):
        # FUXA finds the SELECT by its id prefix (S-HXS_...) and fills the
        # <OPTION> list from the item's ranges there.
        uid = wid.split("_", 1)[1]
        parts.append(
            f'<g id="{wid}" type="svg-ext-html_select" text-anchor="left" font-family="sans-serif" '
            'font-size="16" fill="#000000">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#eef2f7" stroke="#cfd8e3"/>'
            f'<foreignObject id="H-HXS_{uid}" x="{x}" y="{y}" width="{w}" height="{h}">'
            f'<SELECT id="S-HXS_{uid}" style="width:100%;height:100%;text-align:left;"></SELECT>'
            '</foreignObject></g>')

    add_select(STATE_ID, 660, 142, 220, 32)
    add_select(FAULT_ID, 660, 192, 220, 32)

    items = {}

    def put(w):
        items[w["id"]] = w

    for sid, tag, label, colour, cx in LAMPS:
        put(widget(sid, "svg-ext-gauge_semaphore", tag, {"ranges": lamp_ranges(colour)}))
    put(widget(STATE_ID, "svg-ext-html_select", "ST_WORD", {"ranges": state_steps()}))
    put(widget(FAULT_ID, "svg-ext-html_select", "FAULT_CODE", {"ranges": fault_steps()}))
    for vid, tag, label, x in COUNTERS:
        put(widget(vid, "svg-ext-value", tag, {}))
    for wid, tag in ((BTN_START, "HMI_START"), (BTN_STOP, "HMI_STOP")):
        w = widget(wid, "svg-ext-html_button", tag, {
            "events": [
                {"type": "mousedown", "action": "onSetValue", "actparam": "1"},
                {"type": "mouseup", "action": "onSetValue", "actparam": "0"},
            ],
        })
        w["name"] = DISPLAY[tag]      # FUXA prints the item name on the button
        put(w)

    return {
        "version": "1.00",
        "name": "Conveyor sorter",
        "devices": {
            DEVICE: {
                "id": DEVICE,
                "name": DEVICE,
                "type": "ModbusTCP",
                "enabled": True,
                # device polling: 1000 ms, so a counter change reaches the screen
                # within one second (TC-28). FUXA's default here is 3 s.
                "polling": 1000,
                "property": {"address": DEVICE_HOST, "port": 502, "slaveid": 1,
                             "connectionOption": "TcpPort", "timeout": 2000},
                "tags": tags(),
            }
        },
        "hmi": {"views": [{
            "id": "view-main",
            "name": "Conveyor sorter",
            "profile": {"width": 1280, "height": 760, "bkcolor": "#f7f9fbff"},
            "items": items,
            "variables": {},
            "svgcontent": ('<svg width="1280" height="760" xmlns="http://www.w3.org/2000/svg" '
                           'xmlns:svg="http://www.w3.org/2000/svg" '
                           'xmlns:html="http://www.w3.org/1999/xhtml">'
                           '<g><title>Layer 1</title>' + "".join(parts) + '</g></svg>'),
        }]},
        "recipes": [],
        "ar": {"enabled": False, "markers": []},
        "server": {"id": "0", "name": "FUXA Server", "type": "FuxaServer",
                   "property": {}, "polling": 1000},
    }


def main():
    project = build()
    OUT.write_text(json.dumps(project, indent=2))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    if "--apply" in sys.argv:
        req = urllib.request.Request(FUXA + "/api/project", method="POST",
                                     data=json.dumps(project).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            print("POST /api/project ->", r.status)


if __name__ == "__main__":
    main()