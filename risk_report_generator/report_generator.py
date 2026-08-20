import json
import re
import urllib.request
import urllib.error
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from risk_report_generator.report_page import render


ROOT = Path(__file__).resolve().parent.parent
DELIVERIES = ROOT / "campaign_launcher/data/deliveries"
EVENTS = ROOT / "interaction_logger/data/events"
REPORTS = ROOT / "risk_report_generator/data/reports"
EVENT_TYPES = {"link_clicked", "form_submitted", "file_downloaded"}
TRACKING_ID = re.compile(r"[a-f0-9]{32}", re.IGNORECASE)


def run(deliveries: Iterable[Any] | None = None) -> dict[str, Path]:
    delivery_list = (
        _coerce_deliveries(deliveries)
        if deliveries is not None
        else _load_deliveries()
    )
    events = _load_events({item["tracking_id"] for item in delivery_list})
    report = _build_report(delivery_list, events)

    REPORTS.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS / "risk-report.json"
    html_path = REPORTS / "risk-report.html"
    _write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _write(html_path, render(report))
    return {"json": json_path, "html": html_path}


def _coerce_deliveries(items: Iterable[Any]) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for item in items:
        names = ("target", "scenario", "tracking_id", "status", "timestamp")
        data = dict(item) if isinstance(item, Mapping) else {
            name: getattr(item, name, "") for name in names
        }
        target = str(data.get("target") or "").strip()
        tracking_id = str(data.get("tracking_id") or "").casefold()
        if not target or not TRACKING_ID.fullmatch(tracking_id):
            continue
        latest[target.casefold()] = {
            "target": target,
            "scenario": str(data.get("scenario") or "unknown").strip(),
            "tracking_id": tracking_id,
            "status": str(data.get("status") or "prepared").casefold(),
            "timestamp": str(data.get("timestamp") or ""),
        }
    if not latest:
        raise FileNotFoundError("Nessun delivery valido da analizzare")
    return [latest[key] for key in sorted(latest)]


def _load_deliveries() -> list[dict[str, str]]:
    items: list[dict[str, Any]] = []
    paths = sorted(
        DELIVERIES.glob("*-delivery-*.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            items.append(data)
    return _coerce_deliveries(items)


def _load_events(allowed_ids: set[str]) -> list[dict[str, str]]:
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for path in sorted(EVENTS.glob("*-events*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        target = str(data.get("target") or "").strip()
        for item in data.get("events", []):
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("type") or "").casefold()
            tracking_id = str(item.get("tracking_id") or "").casefold()
            timestamp = str(item.get("timestamp") or "")
            if event_type not in EVENT_TYPES or tracking_id not in allowed_ids:
                continue
            if not _valid_date(timestamp):
                continue
            key = (tracking_id, event_type, timestamp)
            unique[key] = {
                "target": target,
                "type": event_type,
                "tracking_id": tracking_id,
                "timestamp": timestamp,
            }
    return sorted(unique.values(), key=lambda item: item["timestamp"])


def _build_report(deliveries: list[dict[str, str]], events: list[dict[str, str]]) -> dict[str, Any]:
    events_by_id: dict[str, list[dict[str, str]]] = {}
    for event in events:
        events_by_id.setdefault(event["tracking_id"], []).append(event)

    targets = []
    for delivery in deliveries:
        target_events = events_by_id.get(delivery["tracking_id"], [])
        types = {event["type"] for event in target_events}
        clicked = "link_clicked" in types
        submitted = "form_submitted" in types
        downloaded = "file_downloaded" in types
        risk = _risk(delivery["status"], clicked, submitted, downloaded)
        targets.append(
            {
                "target": delivery["target"],
                "scenario": delivery["scenario"],
                "delivery_status": delivery["status"],
                "delivery_timestamp": delivery["timestamp"],
                "clicked": clicked,
                "form_submitted": submitted,
                "file_downloaded": downloaded,
                "event_count": len(target_events),
                "first_interaction": target_events[0]["timestamp"] if target_events else None,
                "last_interaction": target_events[-1]["timestamp"] if target_events else None,
                "risk_level": risk,
                "training_recommendation": _recommendation(risk, delivery["scenario"], len(target_events)),
            }
        )

    sent = [target for target in targets if target["delivery_status"] == "sent"]
    clicked_sent = [target for target in sent if target["clicked"]]
    submitted_sent = [target for target in clicked_sent if target["form_submitted"]]
    downloaded_sent = [target for target in clicked_sent if target["file_downloaded"]]
    risks = Counter(target["risk_level"] for target in targets)
    scenario_metrics = _scenario_metrics(targets)

    metrics = {
        "deliveries_analyzed": len(targets),
        "emails_sent": len(sent),
        "emails_prepared": sum(x["delivery_status"] == "prepared" for x in targets),
        "emails_failed": sum(x["delivery_status"] == "failed" for x in targets),
        "targets_interacted": sum(target["event_count"] > 0 for target in targets),
        "targets_clicked": sum(target["clicked"] for target in targets),
        "targets_submitted_form": sum(x["form_submitted"] for x in targets),
        "targets_downloaded_file": sum(x["file_downloaded"] for x in targets),
        "click_rate_pct": _rate(len(clicked_sent), len(sent)),
        "submission_rate_pct": _rate(len(submitted_sent), len(clicked_sent)),
        "download_rate_pct": _rate(len(downloaded_sent), len(clicked_sent)),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": (
            "Le percentuali usano target unici e solo delivery con stato sent. "
            "Gli invii del form sono eventi simulati: nessuna credenziale viene "
            "letta o conservata. Refresh e click ripetuti restano nella timeline "
            "ma non aumentano le percentuali. Le raccomandazioni sono generate da AI."
        ),
        "metrics": metrics,
        "risk_summary": {x: risks.get(x, 0) for x in ("high", "medium", "low", "not_assessed")},
        "high_risk_targets": [x["target"] for x in targets if x["risk_level"] == "high"],
        "interacted_targets": [x["target"] for x in targets if x["event_count"] > 0],
        "scenario_metrics": scenario_metrics,
        "targets": targets,
        "timeline": [
            {
                "timestamp": event["timestamp"],
                "target": next(x["target"] for x in deliveries if x["tracking_id"] == event["tracking_id"]),
                "event": event["type"],
            }
            for event in events
        ],
        "training_recommendations": _overall_recommendations(risks, targets),
    }


def _scenario_metrics(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    scenarios = sorted({str(target["scenario"]) for target in targets})
    for scenario in scenarios:
        rows = [target for target in targets if target["scenario"] == scenario]
        sent = [target for target in rows if target["delivery_status"] == "sent"]
        clicked = [target for target in sent if target["clicked"]]
        output.append(
            {
                "scenario": scenario,
                "sent": len(sent),
                "clicked": len(clicked),
                "submitted_form": sum(x["form_submitted"] for x in clicked),
                "downloaded_file": sum(x["file_downloaded"] for x in clicked),
                "click_rate_pct": _rate(len(clicked), len(sent)),
            }
        )
    return output


def _risk(status: str, clicked: bool, submitted: bool, downloaded: bool) -> str:
    if submitted:
        return "high"
    if clicked or downloaded:
        return "medium"
    if status == "sent":
        return "low"
    return "not_assessed"


def _ask_gemma(prompt: str) -> str:
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "gemma2",
        "prompt": prompt,
        "stream": False
    }
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode("utf-8"), 
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("response", "").strip()
    except urllib.error.URLError:
        return "Servizio AI temporaneamente non disponibile."


def _recommendation(level: str, scenario: str, event_count: int) -> str:
    if level == "not_assessed":
        return "Nessuna valutazione comportamentale: il delivery non risulta inviato."
    
    prompt = (
        f"Sei un esperto di sicurezza informatica. Scrivi un breve consiglio di formazione "
        f"(massimo 20 parole) per un dipendente. "
        f"Contesto: simulazione di phishing a tema '{scenario}'. "
        f"Livello di rischio assegnato: {level}. Interazioni registrate: {event_count}. "
        f"Scrivi solo il consiglio in italiano in tono professionale. NON usare nessuna formattazione "
        f"(niente asterischi, niente grassetti)."
    )
    risposta = _ask_gemma(prompt)
    # Pulizia di sicurezza
    return risposta.replace("**", "").replace("*", "").strip()


def _overall_recommendations(risks: Counter[str], targets: list[dict[str, Any]]) -> list[str]:
    if all(target["event_count"] == 0 for target in targets):
        return ["Ripetere la misurazione solo dopo aver verificato la consegna delle email."]
    
    prompt = (
        f"Sei un CISO aziendale. Analizza questi risultati di una simulazione phishing: "
        f"{risks['high']} utenti ad alto rischio, {risks['medium']} a rischio medio, "
        f"{risks['low']} a basso rischio. "
        f"Genera 3 raccomandazioni strategiche su come migliorare la formazione aziendale. "
        f"Non fare premesse e NON usare formattazione (niente asterischi, niente grassetti, niente numeri o trattini iniziali)."
    )
    risposta = _ask_gemma(prompt)
    
    lines = []
    for line in risposta.split('\n'):
        # Rimuove eventuali asterischi rimasti
        line = line.replace("**", "").replace("*", "").strip()
        # Usa le regex (già importate in alto) per tagliare trattini o numeri a inizio riga
        line = re.sub(r'^[\d\.\-\s]+', '', line).strip()
        if line:
            lines.append(line)
            
    return lines


def _write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _valid_date(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False