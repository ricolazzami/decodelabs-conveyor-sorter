#!/usr/bin/env python3
"""Build the two PDFs in docs/ from their Markdown/ST sources.

    python3 docs/build.py            # -> docs/report.pdf, docs/printout.pdf

HTML is rendered here (a small Markdown subset: headings, tables, lists, code,
images, links) and Chrome turns it into A4 PDFs with a running header and page
numbers, so no Python PDF dependency is added. The HTML is a throwaway in a
temporary directory; the PDFs are the artefacts.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WORK = Path(tempfile.mkdtemp(prefix="conveyor-pdf-"))
PORT = 9231

CSS = """
@page { size: A4; }
html { -webkit-print-color-adjust: exact; }
body { font-family: "DejaVu Serif", Georgia, serif; font-size: 10.2pt; line-height: 1.45;
       color: #16212b; margin: 0; }
h1, h2, h3, h4 { font-family: "DejaVu Sans", Arial, sans-serif; color: #12212f; }
h1 { font-size: 27pt; line-height: 1.15; margin: 9vh 0 14pt; }
h2 { font-size: 15pt; margin: 20pt 0 8pt; padding-bottom: 3pt; border-bottom: 1.5pt solid #cfd8e0;
     break-after: avoid; }
h3 { font-size: 11.6pt; margin: 14pt 0 5pt; break-after: avoid; }
p { margin: 0 0 8pt; text-align: left; }
ul, ol { margin: 0 0 8pt 0; padding-left: 16pt; }
li { margin-bottom: 3pt; }
table { width: 100%; border-collapse: collapse; font-family: "DejaVu Sans", Arial, sans-serif;
        font-size: 8.2pt; line-height: 1.3; margin: 6pt 0 12pt; }
th { text-align: left; background: #eef2f6; border-bottom: 1pt solid #9fb0c0; padding: 3.5pt 4pt; }
td { border-bottom: 0.5pt solid #d8e0e7; padding: 2.8pt 4pt; vertical-align: top; }
tr { break-inside: avoid; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 0.86em; background: #f2f5f8;
       padding: 0 2pt; border-radius: 2pt; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 8pt; line-height: 1.35;
      background: #f6f8fa; border: 0.5pt solid #dde4ea; padding: 6pt 8pt; margin: 6pt 0 10pt;
      white-space: pre-wrap; break-inside: avoid; }
pre.small { font-size: 7.4pt; }
img { max-width: 100%; display: block; margin: 6pt auto 8pt; }
img.sm { max-width: 97%; }
a { color: #14507a; text-decoration: none; }
blockquote { margin: 6pt 0 10pt; padding: 4pt 10pt; border-left: 2.5pt solid #9fb0c0;
             color: #33414f; background: #f7f9fb; }
.pb { break-after: page; }
.lead { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10.5pt; color: #44525f; }
/* code listing */
.line { display: grid; grid-template-columns: 3.1em 1fr; font-family: "DejaVu Sans Mono", monospace;
        font-size: 8.1pt; line-height: 1.32; }
.line .n { color: #93a2b0; text-align: right; padding-right: 8pt; user-select: none; }
.line .t { white-space: pre-wrap; word-break: break-word; }
.line:nth-child(even) { background: #fafbfc; }
.com { color: #4d6b4d; }
.title-block h1 { margin-top: 0; }
.title-block { border-bottom: 2pt solid #12212f; padding-bottom: 8pt; margin-bottom: 14pt; }
.meta { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9pt; color: #44525f; }
"""


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"&lt;(https?://[^&]+?)&gt;", r'<a href="\1">\1</a>', text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img class="sm" alt="\1" src="\2">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def rows(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_html(md: str, base: Path | None = None) -> str:
    out, para, lst = [], [], None

    if base is not None:
        def fix(m):
            src = m.group(2)
            if "://" in src or src.startswith("/"):
                return m.group(0)
            return f'![{m.group(1)}]({(base / src).resolve().as_uri()})'
        md = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", fix, md)

    def flush():
        nonlocal para, lst
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []
        if lst:
            out.append(f"</{lst}>")
            lst = None

    lines, in_list = [], False
    for ln in md.splitlines():
        if re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            lines.append(ln)
            in_list = True
        elif ln.strip() and in_list and ln.startswith(" "):
            lines[-1] = lines[-1].rstrip() + " " + ln.strip()
        else:
            lines.append(ln)
            if not ln.strip():
                in_list = False

    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s == "<!-- pagebreak -->":
            flush()
            out.append('<div class="pb"></div>')
        elif s.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            cls = ' class="small"' if len(code) > 25 else ""
            out.append(f"<pre{cls}>" + "\n".join(
                c.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for c in code) + "</pre>")
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            head = rows(lines[i])
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(rows(lines[i]))
                i += 1
            i -= 1
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
        elif m := re.match(r"^(#{1,4})\s+(.*)$", s):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif re.match(r"^(---|\*\*\*)$", s):
            flush()
            out.append("<hr>")
        elif s.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip("> ").strip())
                i += 1
            i -= 1
            out.append("<blockquote>" + inline(" ".join(quote)) + "</blockquote>")
        elif m := re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln):
            kind = "ul" if m.group(2) in "-*" else "ol"
            if lst != kind:
                flush()
                lst = kind
                out.append(f"<{kind}>")
            out.append(f"<li>{inline(m.group(3))}</li>")
        elif not s:
            flush()
        else:
            if lst and not ln.startswith(" "):
                flush()
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)


def page(title: str, body: str, header_left: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>{CSS}</style></head><body>{body}</body></html>"""


def printout_html() -> str:
    src = ROOT / "src/conveyor-sorter/conveyor-sorter/pous/programs/main.st"
    lines = src.read_text().splitlines()
    body = [
        '<div class="title-block">',
        "<h1>Conveyor sorter — program listing</h1>",
        '<p class="meta">Project 4, DecodeLabs Robotics &amp; Automation · IEC 61131-3 '
        "Structured Text state machine · OpenPLC Runtime v4</p>",
        '<p class="meta">File: src/conveyor-sorter/conveyor-sorter/pous/programs/main.st · '
        f"{len(lines)} lines · all tags and state branches commented (TC-33)</p>",
        "</div>",
    ]
    for n, raw in enumerate(lines, 1):
        txt = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        txt = re.sub(r"(\(\*.*?\*\))", r'<span class="com">\1</span>', txt)
        body.append(f'<div class="line"><span class="n">{n}</span><span class="t">{txt}</span></div>')
    return page("Program listing", "".join(body), "main.st")


def print_pdf(html_path: Path, pdf_path: Path, header_left: str) -> None:
    chrome = subprocess.Popen(
        ["google-chrome-stable", "--headless=new", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
         "--user-data-dir=" + tempfile.mkdtemp(prefix="chrome-pdf-"), "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                target = next((t for t in tabs if t.get("type") == "page"), None)
                if target:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not target:
            raise SystemExit("no chrome debug target")
        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60)
        seq = [0]

        def call(method, **params):
            seq[0] += 1
            ws.send(json.dumps({"id": seq[0], "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == seq[0]:
                    return msg.get("result", {})

        call("Page.enable")
        call("Page.navigate", url=html_path.as_uri())
        time.sleep(1.5)
        call("Runtime.evaluate", expression="document.fonts.ready.then(()=>1)", awaitPromise=True)
        header = ('<div style="font:7.5pt DejaVu Sans;color:#7a8794;width:100%;padding:0 18mm;'
                  'display:flex;justify-content:space-between;">'
                  f'<span>{header_left}</span><span>DecodeLabs · Project 4 · 2026-11-17</span></div>')
        footer = ('<div style="font:7.5pt DejaVu Sans;color:#7a8794;width:100%;padding:0 18mm;'
                  'display:flex;justify-content:space-between;">'
                  '<span>github.com/ricolazzami/decodelabs-conveyor-sorter</span>'
                  '<span class="pageNumber"></span>/<span class="totalPages"></span></div>')
        res = call("Page.printToPDF", printBackground=True, paperWidth=8.27, paperHeight=11.69,
                   marginTop=0.62, marginBottom=0.62, marginLeft=0.67, marginRight=0.67,
                   displayHeaderFooter=True, headerTemplate=header, footerTemplate=footer,
                   preferCSSPageSize=False)
        pdf_path.write_bytes(base64.b64decode(res["data"]))
        ws.close()
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    report_md = (DOCS / "REPORT.md").read_text()
    report_html = page("Conveyor sorter — report", md_to_html(report_md, DOCS), "Conveyor sorter — Project 4 report")
    (WORK / "report.html").write_text(report_html)
    listing = printout_html()
    (WORK / "printout.html").write_text(listing)

    print_pdf(WORK / "report.html", DOCS / "report.pdf", "Conveyor sorter — Project 4 report")
    print_pdf(WORK / "printout.html", DOCS / "printout.pdf", "Conveyor sorter — main.st")
    for p in (DOCS / "report.pdf", DOCS / "printout.pdf"):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())