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
SUBMIT_PATH = re.compile(r"/submit/([a-f0-9]{32})/?", re.IGNORECASE)
DOWNLOAD_PATH = re.compile(r"/download/([a-f0-9]{32})/?", re.IGNORECASE)
WRITE_LOCK = Lock()
EVENT_TYPES = {"link_clicked", "form_submitted", "file_downloaded"}
STYLE = """
body{font-family:Arial,sans-serif;max-width:680px;margin:70px auto;padding:24px;color:#172033}
main{border:1px solid #d9deea;border-radius:14px;padding:32px;box-shadow:0 8px 30px #17203312}
h1{margin-top:0}p{line-height:1.55}label{display:block;margin:18px 0 6px}
input{box-sizing:border-box;width:100%;padding:12px;border:1px solid #b8c0d2;border-radius:7px}
button,a.button{display:inline-block;margin-top:20px;padding:12px 18px;border:0;border-radius:7px;
background:#2149b8;color:white;text-decoration:none;cursor:pointer}.note{font-size:.88rem;color:#596176}
"""
DOWNLOAD_CONTENT = (
    "Simulazione di sicurezza completata.\n\n"
    "Questo file è innocuo e fa parte del progetto di tesi di Mario Lezzi.\n"
    "Non sono state raccolte credenziali o altre informazioni personali.\n"
)


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

            delivery, tracking_id = _match(path, TRACK_PATH, deliveries)
            if delivery:
                _save_event(delivery, tracking_id, "link_clicked")
                print(f"      Click registrato: {delivery['target']}")
                self._reply(200, _login_page(tracking_id), "text/html; charset=utf-8")
                return

            delivery, tracking_id = _match(path, DOWNLOAD_PATH, deliveries)
            if delivery:
                _save_event(delivery, tracking_id, "file_downloaded")
                print(f"      Download registrato: {delivery['target']}")
                self._reply(
                    200,
                    DOWNLOAD_CONTENT,
                    "text/plain; charset=utf-8",
                    {"Content-Disposition": 'attachment; filename="simulation-report.txt"'},
                )
                return

            self._reply(404, "Collegamento non valido", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            delivery, tracking_id = _match(path, SUBMIT_PATH, deliveries)
            if not delivery:
                self._reply(404, "Collegamento non valido", "text/plain; charset=utf-8")
                return

            _save_event(delivery, tracking_id, "form_submitted")
            print(f"      Form registrato: {delivery['target']}")
            self._reply(
                200,
                _completion_page(tracking_id),
                "text/html; charset=utf-8",
            )

        def _reply(
            self,
            status: int,
            content: str,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    return Handler


def _match(
    path: str,
    pattern: re.Pattern[str],
    deliveries: dict[str, dict[str, str]],
) -> tuple[dict[str, str] | None, str]:
    match = pattern.fullmatch(path)
    tracking_id = match.group(1).casefold() if match else ""
    return deliveries.get(tracking_id), tracking_id


def _page(title: str, content: str) -> str:
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{STYLE}</style></head><body><main>{content}</main></body></html>"""


def _login_page(tracking_id: str) -> str:
    return _page(
        "Verifica attività",
        f"""<h1>Verifica attività recente</h1>
<p>Per continuare, conferma i dati dell'account.</p>
<form method="post" action="/submit/{tracking_id}" autocomplete="off">
<label>Email</label><input type="email" autocomplete="off" required>
<label>Password</label><input type="password" autocomplete="new-password" required>
<button type="submit">Continua</button></form>
<p class="note">Simulazione controllata: i valori digitati restano nel browser e non
vengono trasmessi né salvati.</p>""",
    )


def _completion_page(tracking_id: str) -> str:
    return _page(
        "Simulazione completata",
        f"""<h1>Simulazione completata</h1>
<p>Hai completato una simulazione controllata realizzata nell'ambito del progetto
di tesi di Mario Lezzi.</p>
<p>Non sono state raccolte credenziali né altri dati personali.</p>
<a class="button" href="/download/{tracking_id}">Scarica il report della simulazione</a>""",
    )


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


def _save_event(
    delivery: dict[str, str],
    tracking_id: str,
    event_type: str,
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Evento non supportato: {event_type}")
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
                "type": event_type,
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