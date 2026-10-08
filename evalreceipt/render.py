"""Portable HTML report with escaped data and no scripts or remote requests."""

from html import escape
from typing import Any


def render_html(report: dict[str, Any]) -> str:
    def text(value: Any) -> str:
        return escape(str(value), quote=True)

    summary = report["summary"]
    issue_rows = "".join(
        f"<tr><td>{text(item.get('case_id', 'Run'))}</td><td>{text(item['code'])}</td><td>{text(item['message'])}</td></tr>"
        for item in report["issues"]
    ) or '<tr><td colspan="3">No integrity issues found.</td></tr>'
    case_rows = "".join(
        f"<tr><td>{text(item['id'])}</td><td>{text(item['status'])}</td><td>{text(item['valid'])}</td><td>{text(item['score'])}</td><td>{text(item['passed'])}</td></tr>"
        for item in report["cases"]
    )
    state = "Evidence consistent" if report["integrity_ok"] else "Evidence needs attention"
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>EvalReceipt — {text(report['run_id'])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f5f7fb;color:#172237;max-width:1100px;margin:48px auto;padding:0 24px}}h1{{font-size:40px;letter-spacing:-1px}}.pill{{display:inline-block;border-radius:99px;background:#e3ebf8;padding:4px 14px}}.cards{{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}}.card{{padding:20px;background:white;border:1px solid #d9e1ec;border-radius:14px;min-width:160px}}strong{{display:block;font-size:28px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #e3e8f0;overflow-wrap:anywhere}}.table-wrap{{overflow-x:auto}}footer{{margin-top:32px;color:#55657c}}</style></head>
<body><p>EvalReceipt / execution evidence</p><h1>{text(report['run_id'])}</h1><p class="pill">{text(state)}</p>
<p>{text(report['started_at'])} → {text(report['finished_at'])}</p>
<div class="cards"><div class="card">Expected cases<strong>{text(summary['expected'])}</strong></div><div class="card">Valid completed<strong>{text(summary['valid_completed'])}</strong></div><div class="card">Semantic passes<strong>{text(summary['semantic_passed'])}</strong></div><div class="card">Integrity findings<strong>{text(summary['issue_count'])}</strong></div></div>
<h2>Case records</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>Execution</th><th>Valid evidence</th><th>Score</th><th>Semantic pass</th></tr></thead><tbody>{case_rows}</tbody></table></div>
<h2>Integrity findings</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>Finding</th><th>Detail</th></tr></thead><tbody>{issue_rows}</tbody></table></div>
<footer>Rates use all expected cases as their denominator. Semantic scores come from the supplied manifest.</footer></body></html>'''
