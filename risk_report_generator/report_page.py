import html
import json
from datetime import datetime, timezone
from typing import Any


def render(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    
    # Preparazione dei dati per i grafici Chart.js
    risk_labels = list(report["risk_summary"].keys())
    risk_values = list(report["risk_summary"].values())
    funnel_data = [
        metrics['emails_sent'], 
        metrics['targets_clicked'], 
        metrics['targets_submitted_form'], 
        metrics['targets_downloaded_file']
    ]

    # Preparazione dati per la Timeline interattiva
    timeline_js_data = []
    for x in report["timeline"]:
        timeline_js_data.append({
            "x": _date(x['timestamp']),
            "y": _event(x['event']),
            "target": x['target']
        })

    targets = "".join(
        "<tr>"
        f"<td><strong>{_h(x['target'])}</strong></td>"
        f"<td>{_h(x['scenario'])}</td>"
        f"<td>{_yes(x['clicked'])}</td><td>{_yes(x['form_submitted'])}</td>"
        f"<td>{_yes(x['file_downloaded'])}</td>"
        f"<td><span class='risk {x['risk_level']}'>{_h(x['risk_level'])}</span></td>"
        f"<td>{_h(x['training_recommendation'])}</td></tr>"
        for x in report["targets"]
    )
    
    scenarios = "".join(
        "<tr>"
        f"<td>{_h(x['scenario'])}</td><td>{x['sent']}</td><td>{x['clicked']}</td>"
        f"<td>{x['submitted_form']}</td><td>{x['downloaded_file']}</td>"
        f"<td>{x['click_rate_pct']:.1f}%</td></tr>"
        for x in report["scenario_metrics"]
    )
    
    tips = "".join(
        f"<li>{_h(item)}</li>" for item in report["training_recommendations"]
    )

    # Iniezione dello script JavaScript e HTML
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirage Risk Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>{STYLE}</style></head><body><main>
<header><h1>Simulazione Completata: Risk Report</h1><div class="muted">Generato il {_h(_date(report['generated_at']))}</div>
<div class="cards"><div class="card"><strong>{metrics['click_rate_pct']:.1f}%</strong>Click rate</div>
<div class="card"><strong>{metrics['submission_rate_pct']:.1f}%</strong>Submission rate</div>
<div class="card"><strong>{metrics['download_rate_pct']:.1f}%</strong>Download rate</div></div></header>
<section class="grid">
    <div>
        <h2>Funnel Interazioni</h2>
        <div class="panel chart-container">
            <canvas id="funnelChart"></canvas>
        </div>
    </div>
    <div>
        <h2>Distribuzione del Rischio</h2>
        <div class="panel chart-container">
            <canvas id="riskChart"></canvas>
        </div>
    </div>
</section>
<h2>Risultati per Target</h2>{_table('Target|Scenario|Click|Form|Download|Rischio|Formazione Consigliata', targets)}
<h2>Prestazioni per Scenario</h2>{_table('Scenario|Inviate|Click|Form|Download|CTR', scenarios)}

<h2>Timeline Eventi</h2>
<div class="panel chart-container" style="height: 300px; padding: 20px;">
    <canvas id="timelineChart"></canvas>
</div>

<h2>Raccomandazioni AI</h2><div class="panel"><ul>{tips}</ul></div>
<p class="panel note">ℹ️ <b>Nota di Sistema:</b> {_h(report['methodology'])}</p>
</main>

<script>
document.addEventListener('DOMContentLoaded', function() {{
    const colorHigh = '#a94442';
    const colorMedium = '#8a6d3b';
    const colorLow = '#3c763d';
    const colorNotAssessed = '#cccccc';
    const colorGold = '#c19b6c';
    const colorMuted = '#8c7a65';
    const colorLine = '#e8dcca';

    // Grafico a Ciambella (Rischio)
    const riskCtx = document.getElementById('riskChart').getContext('2d');
    new Chart(riskCtx, {{
        type: 'doughnut',
        data: {{
            labels: {json.dumps(risk_labels)},
            datasets: [{{
                data: {json.dumps(risk_values)},
                backgroundColor: [colorHigh, colorMedium, colorLow, colorNotAssessed],
                borderWidth: 0,
                hoverOffset: 4
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ position: 'right', labels: {{ font: {{ family: "'Segoe UI', sans-serif" }} }} }}
            }},
            cutout: '70%'
        }}
    }});

    // Grafico a Barre (Funnel)
    const funnelCtx = document.getElementById('funnelChart').getContext('2d');
    new Chart(funnelCtx, {{
        type: 'bar',
        data: {{
            labels: ['Inviate', 'Click', 'Form Dati', 'Download'],
            datasets: [{{
                label: 'Numero di Target',
                data: {json.dumps(funnel_data)},
                backgroundColor: colorGold,
                borderRadius: 4
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                y: {{ beginAtZero: true, ticks: {{ stepSize: 1, precision: 0 }} }}
            }}
        }}
    }});

    // Grafico Scatter (Timeline Eventi)
    const timelineData = {json.dumps(timeline_js_data)};
    const timelineXLabels = [...new Set(timelineData.map(d => d.x))];
    const timelineCtx = document.getElementById('timelineChart').getContext('2d');
    
    if (timelineData.length > 0) {{
        new Chart(timelineCtx, {{
            type: 'scatter',
            data: {{
                datasets: [{{
                    label: 'Interazioni',
                    data: timelineData,
                    pointBackgroundColor: colorGold,
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 8,
                    pointHoverRadius: 11
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        type: 'category',
                        labels: timelineXLabels,
                        ticks: {{ display: false }}, // Nasconde le date per pulire il grafico
                        grid: {{ color: colorLine, drawBorder: false }}
                    }},
                    y: {{
                        type: 'category',
                        labels: ['Link aperto', 'Form sottomesso', 'Download avviato'],
                        offset: true,
                        ticks: {{ color: colorMuted, font: {{ weight: 'bold' }} }},
                        grid: {{ color: colorLine, drawBorder: false }}
                    }}
                }},
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        backgroundColor: 'rgba(58, 46, 36, 0.95)',
                        titleFont: {{ size: 14 }},
                        bodyFont: {{ size: 14 }},
                        padding: 14,
                        callbacks: {{
                            title: function(context) {{
                                return "Data e Ora: " + context[0].raw.x;
                            }},
                            label: function(context) {{
                                const d = context.raw;
                                return "Target: " + d.target;
                            }}
                        }}
                    }}
                }}
            }}
        }});
    }} else {{
        document.getElementById('timelineChart').outerHTML = "<p style='text-align:center; color:var(--muted); margin-top:130px;'>Nessuna interazione registrata.</p>";
    }}
}});
</script>
</body></html>"""


def _table(headers: str, rows: str) -> str:
    head = "".join(f"<th>{_h(item)}</th>" for item in headers.split("|"))
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'


def _date(value: str) -> str:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return value


def _event(value: str) -> str:
    return {
        "link_clicked": "Link aperto",
        "form_submitted": "Form sottomesso",
        "file_downloaded": "Download avviato",
    }.get(value, value)


def _width(value: int, total: int) -> float:
    return max(2.0 if value else 0.0, value / total * 100 if total else 0.0)


def _yes(value: bool) -> str:
    return "<span class='check-yes'>✓</span>" if value else "<span class='check-no'>✗</span>"


def _h(value: Any) -> str:
    return html.escape(str(value))


STYLE = """
:root{
  --ink:#3a2e24; 
  --muted:#8c7a65; 
  --line:#e8dcca; 
  --gold-grad:linear-gradient(135deg, #c19b6c, #8b662d); 
  --bg:#f9f8f4; 
  --card-bg:#ffffff;
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;}
main{max-width:1120px;margin:32px auto;padding:0 20px 48px} 
header{background:var(--card-bg);color:var(--ink);padding:32px;border-radius:16px;border:1px solid var(--line);box-shadow: 0 4px 24px rgba(139,102,45,0.08);}
h1{margin:0 0 8px; font-weight: 300; letter-spacing: 1px;} h2{margin:32px 0 14px; font-weight: 400; text-transform: uppercase; font-size: 16px; letter-spacing: 1.5px; color: #8b662d;}
.muted{color:var(--muted); font-size: 14px;}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:24px}
.card,.panel{background:var(--card-bg);border:1px solid var(--line);border-radius:12px;padding:24px;box-shadow: 0 4px 16px rgba(0,0,0,0.03);}
.card strong{display:block;font-size:32px;background: var(--gold-grad); -webkit-background-clip: text; -webkit-text-fill-color: transparent;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.scroll{overflow:auto}
.chart-container{height: 250px; position: relative; display: flex; justify-content: center; align-items: center;}
table{width:100%;border-collapse:collapse;background:var(--card-bg); border-radius: 12px; overflow: hidden; box-shadow: 0 4px 16px rgba(0,0,0,0.03);}
th,td{padding:16px;border-bottom:1px solid var(--line);text-align:left;vertical-align:center}
th{background:#fdfcf8;font-size:13px; font-weight: 600; text-transform: uppercase; color: var(--muted); letter-spacing: 1px;}
.check-yes{color:#4a7c59; font-weight:bold; font-size:18px;}
.check-no{color:#a94442; font-weight:bold; font-size:18px;}
.risk{padding:4px 10px;border-radius:6px;font-size: 12px; font-weight:bold; text-transform: uppercase;}
.risk.high{background:#f2dede;color:#a94442}
.risk.medium{background:#fcf8e3;color:#8a6d3b}
.risk.low{background:#dff0d8;color:#3c763d}
.risk.not_assessed{background:#f5f5f5;color:#777777}
.note{color:var(--muted); font-size: 13px; background: transparent; border: none; box-shadow: none; padding: 0; margin-top: 24px;}
ul{line-height:1.7; padding-left: 20px;}
@media(max-width:760px){.cards,.grid{grid-template-columns:1fr}table{font-size:13px}th,td{padding:10px}}
"""