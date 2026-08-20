import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DELIVERIES = ROOT / "campaign_launcher/data/deliveries"
EVENTS = ROOT / "interaction_logger/data/events"
TRACK_PATH = re.compile(r"/track/([a-f0-9]{32})/?", re.IGNORECASE)
WRITE_LOCK = Lock()
LANDING_PAGE = """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Simulazione completata</title>
  <style>
    body {font-family:Arial,sans-serif;max-width:680px;margin:80px auto;padding:24px;color:#172033}
    main {border:1px solid #d9deea;border-radius:14px;padding:32px;box-shadow:0 8px 30px #17203312}
    h1 {margin-top:0} p {line-height:1.6}
  </style>
</head>
<body><main>
  <h1>Simulazione completata</h1>
  <p>Questo collegamento fa parte di una simulazione controllata realizzata
  nell'ambito del progetto di tesi di Mario Lezzi.</p>
  <p>Non sono state richieste o raccolte credenziali né altri dati personali.</p>
</main></body>
</html>"""


def run(host: str | None = None, port: int | None = None) -> None:
    deliveries = _load_deliveries()
    address = host or os.getenv("LOGGER_HOST", "127.0.0.1")
    server_port = port or int(os.getenv("LOGGER_PORT", "8000"))
    server = ThreadingHTTPServer((address, server_port), _handler(deliveries))
    print(f"      Server attivo: http://localhost:{server_port}")
    print("      Premi Ctrl+C per arrestarlo")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n      Interaction Logger arrestato")
    finally:
        server.server_close()


def _handler(deliveries: dict[str, dict[str, str]]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._reply(200, "OK", "text/plain; charset=utf-8")
                return

            match = TRACK_PATH.fullmatch(path)
            delivery = deliveries.get(match.group(1).casefold()) if match else None
            if not delivery:
                self._reply(404, "Collegamento non valido", "text/plain; charset=utf-8")
                return

            _save_event(
                delivery,
                match.group(1).casefold(),
            )
            print(f"      Click registrato: {delivery['target']}")
            self._reply(200, LANDING_PAGE, "text/html; charset=utf-8")

        def _reply(self, status: int, content: str, content_type: str) -> None:
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    return Handler


def _load_deliveries() -> dict[str, dict[str, str]]:
    deliveries: dict[str, dict[str, str]] = {}
    for path in DELIVERIES.glob("*-delivery-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        tracking_id = str(data.get("tracking_id") or "").casefold()
        target = str(data.get("target") or "").strip()
        if re.fullmatch(r"[a-f0-9]{32}", tracking_id) and target:
            deliveries[tracking_id] = {"target": target}
    if not deliveries:
        raise FileNotFoundError(f"Nessun delivery valido in {DELIVERIES}")
    return deliveries


def _save_event(delivery: dict[str, str], tracking_id: str) -> None:
    EVENTS.mkdir(parents=True, exist_ok=True)
    path = EVENTS / f"{_slug(delivery['target'])}-events.json"
    with WRITE_LOCK:
        data = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"target": delivery["target"], "events": []}
        )
        data["events"].append(
            {
                "type": "link_clicked",
                "tracking_id": tracking_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "target"