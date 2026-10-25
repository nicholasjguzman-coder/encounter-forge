"""Build the self-contained report.html from report/report_template.html.

Fills in measured tables from report_assets/*.json and inlines every image as a base64 data URI,
so the HTML opens by double-click on any computer.
Usage: python tools/build_report.py
"""
import base64
import html
import json
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "report_assets"
REPO_URL = "https://github.com/nicholasjguzman-coder/encounter-forge"


def pct(x):
    return f"{x * 100:.0f}%" if x >= 0.01 or x == 0 else "<1%"


def main():
    s = (ROOT / "report" / "report_template.html").read_text()
    m = json.loads((A / "measurements.json").read_text())
    bench = json.loads((A / "benchmarks.json").read_text())

    rows = "".join(
        f"<tr><td>{html.escape(b['case'])}</td><td>{b['official']}</td><td class='n'>{pct(b['tpk'])}</td>"
        f"<td class='n'>{pct(b['down'])}</td></tr>\n" for b in bench)
    names = {"parser": "① Encounter Parser", "items": "② Items &amp; Effects", "tactician": "③ Tactician",
             "compiler:custom": "④ Rules Compiler (custom creature)", "explainer": "⑤ Explainer", "tuner": "⑥ Tuner"}
    agent_rows = "".join(
        f"<tr><td>{names[k]}</td><td class='n'>{v['n']}</td><td class='n'>{v['input']:,}</td><td class='n'>{v['cached']:,}</td>"
        f"<td class='n'>{v['output']:,}</td><td class='n'>${v['cost']:.4f}</td><td class='n'>{v['seconds']:.1f} s</td></tr>\n"
        for k, v in sorted(m["agents"].items(), key=lambda kv: list(names).index(kv[0])))
    nt = m["n_tradeoff"]
    ks = sorted(nt, key=int)
    s = (s.replace("{{BENCH_ROWS}}", rows).replace("{{AGENT_ROWS}}", agent_rows)
         .replace("{{N_HEAD}}", "".join(f"<th class='n'>{int(k):,}</th>" for k in ks))
         .replace("{{N_MEAN}}", "".join(f"<td class='n'>{nt[k]['mean_p_down'] * 100:.1f}%</td>" for k in ks))
         .replace("{{N_SD}}", "".join(f"<td class='n'>±{nt[k]['sd_across_seeds'] * 100:.1f}</td>" for k in ks))
         .replace("{{N_SEC}}", "".join(f"<td class='n'>{nt[k]['seconds']:.1f}</td>" for k in ks))
         .replace("{{PYTEST}}", html.escape((A / "pytest_output.txt").read_text().strip()))
         .replace("{{REPO_URL}}", REPO_URL))

    def inline(match):
        p = ROOT / match.group(1)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()

    s = re.sub(r"\{\{img:([^}]+)\}\}", inline, s)
    left = re.findall(r"\{\{[^}]+\}\}", s)
    assert not left, f"unfilled placeholders: {left}"
    out = ROOT / "report.html"
    out.write_text(s)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
