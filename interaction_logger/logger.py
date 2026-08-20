import json
import os
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.parse import urlparse

from interaction_logger.pages import completion_page, landing_page, page_type
from interaction_logger.tunnel import PublicTunnel, open_tunnel


ROOT = Path(__file__).resolve().parent.parent
DELIVERIES = ROOT / "campaign_launcher/data/deliveries"
EVENTS = ROOT / "interaction_logger/data/events"
TRACK_PATH = re.compile(r"/track/([a-f0-9]{32})/?", re.IGNORECASE)
SUBMIT_PATH = re.compile(r"/submit/([a-f0-9]{32})/?", re.IGNORECASE)
DOWNLOAD_PATH = re.compile(r"/download/([a-f0-9]{32})/?", re.IGNORECASE)
EVENT_TYPES = {"link_clicked", "form_submitted", "file_downloaded"}
WRITE_LOCK = Lock()
DOWNLOAD_CONTENT = (
    "Simulazione di sicurezza completata.\n\n"
    "Questo file è innocuo e fa parte del progetto di tesi di Mario Lezzi.\n"
    "Non sono state raccolte credenziali o altre informazioni personali.\n"
)


class _DeliveryScope:
    def __init__(self, tracking_ids: set[str] | None) -> None:
        self.restricted = tracking_ids is not None
        self.tracking_ids = tracking_ids or set()
        self.lock = Lock()

    def allows(self, tracking_id: str) -> bool:
        with self.lock:
            return not self.restricted or tracking_id in self.tracking_ids

    def replace(self, deliveries: Iterable[Any]) -> None:
        tracking_ids = _delivery_ids(deliveries)
        if not tracking_ids:
            raise ValueError("Nessun delivery valido da registrare")
        with self.lock:
            self.restricted = True
            self.tracking_ids = tracking_ids


class LoggerSession:
    def __init__(
        self,
        server: ThreadingHTTPServer,
        thread: Thread,
        scope: _DeliveryScope,
        *,
        tunnel: PublicTunnel | None = None,
        public_url: str | None = None,
    ) -> None:
        self.server = server
        self.thread = thread
        self.scope = scope
        self.tunnel = tunnel
        self.public_url = public_url
        self.port = int(server.server_address[1])
        self._closed = False

    @property
    def tracking_base_url(self) -> str:
        root = self.public_url or f"http://localhost:{self.port}"
        return f"{root.rstrip('/')}/track"

    def authorize(self, deliveries: Iterable[Any]) -> None:
        self.scope.replace(deliveries)

    def wait(self) -> None:
        print("      Premi Ctrl+C per arrestarlo")
        try:
            while self.thread.is_alive():
                self.thread.join(0.5)
        except KeyboardInterrupt:
            print("\n      Interaction Logger arrestato")
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.tunnel:
            self.tunnel.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def start(
    host: str | None = None,
    port: int | None = None,
    *,
    public: bool = False,
    deliveries: Iterable[Any] | None = None,
) -> LoggerSession:
    _load_env()
    address = host or os.getenv("LOGGER_HOST", "127.0.0.1")
    server_port = port if port is not None else int(os.getenv("LOGGER_PORT", "8000"))
    initial_ids = _delivery_ids(deliveries) if deliveries is not None else None
    if public and deliveries is None:
        initial_ids = set()
    scope = _DeliveryScope(initial_ids)
    server = ThreadingHTTPServer((address, server_port), _handler(scope))
    actual_port = int(server.server_address[1])
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"      Server attivo: http://localhost:{actual_port}")

    session = LoggerSession(server, thread, scope)
    if not public:
        return session

    try:
        tunnel = open_tunnel(ROOT, actual_port)
        session.tunnel = tunnel
        session.public_url = tunnel.url
        print(f"      Tunnel pubblico: {tunnel.url}")
        return session
    except Exception:
        session.close()
        raise


def run(
    host: str | None = None,
    port: int | None = None,
    *,
    deliveries: Iterable[Any] | None = None,
) -> None:
    start(host, port, deliveries=deliveries).wait()


def _handler(scope: _DeliveryScope):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._reply(200, "OK", "text/plain; charset=utf-8")
                return

            delivery, tracking_id = _match(path, TRACK_PATH, scope)
            if delivery:
                _save_event(delivery, tracking_id, "link_clicked")
                print(
                    f"      Click registrato: {delivery['target']} "
                    f"[{delivery['scenario']}]"
                )
                self._reply(
                    200,
                    landing_page(delivery, tracking_id),
                    "text/html; charset=utf-8",
                )
                return

            delivery, tracking_id = _match(path, DOWNLOAD_PATH, scope)
            if delivery and page_type(delivery) == "document":
                _save_event(delivery, tracking_id, "file_downloaded")
                print(f"      Download registrato: {delivery['target']}")
                self._reply(
                    200,
                    DOWNLOAD_CONTENT,
                    "text/plain; charset=utf-8",
                    {"Content-Disposition": 'attachment; filename="simulation-report.txt"'},
                )
                return

            self._not_found()

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            delivery, tracking_id = _match(path, SUBMIT_PATH, scope)
            if not delivery or page_type(delivery) == "document":
                self._not_found()
                return

            _save_event(delivery, tracking_id, "form_submitted")
            print(f"      Form registrato: {delivery['target']}")
            self._reply(
                200,
                completion_page(delivery),
                "text/html; charset=utf-8",
            )

        def _not_found(self) -> None:
            self._reply(404, "Collegamento non valido", "text/plain; charset=utf-8")

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
    scope: _DeliveryScope,
) -> tuple[dict[str, str] | None, str]:
    match = pattern.fullmatch(path)
    tracking_id = match.group(1).casefold() if match else ""
    if not tracking_id or not scope.allows(tracking_id):
        return None, tracking_id
    return _load_deliveries(required=False).get(tracking_id), tracking_id


def _load_deliveries(*, required: bool = True) -> dict[str, dict[str, str]]:
    deliveries: dict[str, dict[str, str]] = {}
    for path in DELIVERIES.glob("*-delivery-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        tracking_id = str(data.get("tracking_id") or "").casefold()
        target = str(data.get("target") or "").strip()
        scenario = str(data.get("scenario") or "").strip().casefold()
        if re.fullmatch(r"[a-f0-9]{32}", tracking_id) and target:
            deliveries[tracking_id] = {"target": target, "scenario": scenario}
    if required and not deliveries:
        raise FileNotFoundError(f"Nessun delivery valido in {DELIVERIES}")
    return deliveries


def _delivery_ids(deliveries: Iterable[Any]) -> set[str]:
    output: set[str] = set()
    for delivery in deliveries:
        value = (
            delivery.get("tracking_id")
            if isinstance(delivery, Mapping)
            else getattr(delivery, "tracking_id", None)
        )
        tracking_id = str(value or "").casefold()
        if re.fullmatch(r"[a-f0-9]{32}", tracking_id):
            output.add(tracking_id)
    return output


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
                "scenario": delivery.get("scenario", ""),
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
