import json
import os
import re
import smtplib
import unicodedata
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Literal
from uuid import uuid4

from nlp_text_personalizer import PersonalizedEmail
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent
MESSAGES = ROOT / "nlp_text_personalizer/data/messages"
DELIVERIES = ROOT / "campaign_launcher/data/deliveries"
SIMULATION_URL = "{{simulation_url}}"
DISCLAIMER = (
    "Nota di trasparenza: questa email fa parte di una simulazione controllata "
    "realizzata nell'ambito del progetto di tesi di Mario Lezzi. Il collegamento "
    "non richiede né raccoglie credenziali o altri dati personali."
)


class Delivery(BaseModel):
    target: str
    recipient: str
    scenario: str
    tracking_id: str
    tracking_url: str
    sender: str
    subject: str
    body: str
    status: Literal["prepared", "sent", "failed"]
    timestamp: str
    error: str = ""


def run(
    dry_run: bool = True,
    base_url: str | None = None,
) -> list[Delivery]:
    _load_env()
    messages = _load_messages()
    base_url = (base_url or os.getenv(
        "SIMULATION_BASE_URL", "http://localhost:8000/track"
    )).rstrip("/")
    smtp = _smtp_settings() if not dry_run else None
    test_recipient = os.getenv("SMTP_TEST_RECIPIENT", "").strip()
    if smtp and test_recipient:
        matching = [
            message
            for message in messages
            if message.recipient.casefold() == test_recipient.casefold()
        ]
        messages = (matching or messages)[:1]
        print(f"  Test SMTP: un solo messaggio verso {test_recipient}")
    deliveries: list[Delivery] = []

    for index, message in enumerate(messages, 1):
        tracking_id = uuid4().hex
        tracking_url = f"{base_url}/{tracking_id}"
        status: Literal["prepared", "sent", "failed"] = "prepared"
        error = ""
        body = message.body
        recipient = test_recipient if smtp and test_recipient else message.recipient

        try:
            if message.body.count(SIMULATION_URL) != 1:
                raise ValueError(f"{SIMULATION_URL} deve comparire una volta")
            body = (
                message.body.replace(SIMULATION_URL, tracking_url)
                + f"\n\n---\n{DISCLAIMER}"
            )
            if smtp:
                _send(message, recipient, body, smtp)
                status = "sent"
        except Exception as exc:
            status, error = "failed", str(exc)

        delivery = Delivery(
            target=message.target,
            recipient=recipient,
            scenario=message.scenario,
            tracking_id=tracking_id,
            tracking_url=tracking_url,
            sender=message.sender,
            subject=message.subject,
            body=body,
            status=status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=error,
        )
        _save(delivery)
        deliveries.append(delivery)
        print(f"  [{index}/{len(messages)}] {message.target}: {status}")

    return deliveries


def _load_messages() -> list[PersonalizedEmail]:
    latest: dict[str, PersonalizedEmail] = {}
    paths = sorted(
        MESSAGES.glob("*-message-*.json"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    for path in paths:
        message = PersonalizedEmail.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        latest[message.target.casefold()] = message
    if not latest:
        raise FileNotFoundError(f"Nessun messaggio personalizzato in {MESSAGES}")
    return [latest[key] for key in sorted(latest)]


def _smtp_settings() -> dict[str, object]:
    host = os.getenv("SMTP_HOST", "").strip()
    from_address = os.getenv("SMTP_FROM", "").strip()
    if not host or "@" not in from_address:
        raise ValueError("Per l'invio live servono SMTP_HOST e SMTP_FROM")
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "from_address": from_address,
        "username": os.getenv("SMTP_USERNAME", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "starttls": os.getenv("SMTP_STARTTLS", "true").strip().casefold()
        not in {"0", "false", "no"},
    }


def _send(
    message: PersonalizedEmail,
    recipient: str,
    body: str,
    smtp: dict[str, object],
) -> None:
    email = EmailMessage()
    email["From"] = formataddr((message.sender, str(smtp["from_address"])))
    email["To"] = recipient
    email["Subject"] = message.subject
    email.set_content(body)

    with smtplib.SMTP(str(smtp["host"]), int(smtp["port"]), timeout=30) as client:
        if smtp["starttls"]:
            client.starttls()
        if smtp["username"]:
            client.login(str(smtp["username"]), str(smtp["password"]))
        client.send_message(email)


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _save(delivery: Delivery) -> None:
    DELIVERIES.mkdir(parents=True, exist_ok=True)
    slug = _slug(delivery.target)
    path = DELIVERIES / f"{slug}-delivery-1.json"
    path.write_text(
        json.dumps(delivery.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for old in DELIVERIES.glob(f"{slug}-delivery-*.json"):
        if old != path:
            old.unlink()


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "target"