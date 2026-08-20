from html import escape
from pathlib import Path


STYLE = Path(__file__).with_name("style.css").read_text(encoding="utf-8")

THEMES = {
    "security": {
        "page": "#f4f7fc", "glow": "#dce7fa", "text": "#1d2b47",
        "muted": "#64708a", "accent": "#3b6fca", "accent-dark": "#244d96",
        "border": "#d5dfef", "surface": "#ffffff", "soft": "#f3f6fb",
        "focus": "59,111,202",
    },
    "repository": {
        "page": "#f3f8f5", "glow": "#d9eadf", "text": "#20352a",
        "muted": "#64776b", "accent": "#467b58", "accent-dark": "#2d5b3d",
        "border": "#d3e2d8", "surface": "#ffffff", "soft": "#f1f6f3",
        "focus": "70,123,88",
    },
    "invoice": {
        "page": "#fbf5f3", "glow": "#efd9d2", "text": "#4a302d",
        "muted": "#806a66", "accent": "#a15c4e", "accent-dark": "#773b31",
        "border": "#ead8d2", "surface": "#ffffff", "soft": "#faf3f1",
        "focus": "161,92,78",
    },
    "document": {
        "page": "#f7f5fb", "glow": "#e4def2", "text": "#332d49",
        "muted": "#746d88", "accent": "#7060a0", "accent-dark": "#514278",
        "border": "#ded9ea", "surface": "#ffffff", "soft": "#f5f3f9",
        "focus": "112,96,160",
    },
    "management": {
        "page": "#f3f5f8", "glow": "#dce2eb", "text": "#202d3d",
        "muted": "#677486", "accent": "#49637f", "accent-dark": "#30465f",
        "border": "#d7dee7", "surface": "#ffffff", "soft": "#f2f4f7",
        "focus": "73,99,127",
    },
    "event": {
        "page": "#f1f8f7", "glow": "#d4ebe7", "text": "#183d39",
        "muted": "#607d79", "accent": "#2d7e74", "accent-dark": "#1c5a53",
        "border": "#d0e4e1", "surface": "#ffffff", "soft": "#eff7f5",
        "focus": "45,126,116",
    },
    "default": {
        "page": "#fdfbf7", "glow": "#f4ead5", "text": "#4a4036",
        "muted": "#81766b", "accent": "#a6845e", "accent-dark": "#7a5f42",
        "border": "#e8dfd5", "surface": "#ffffff", "soft": "#faf9f7",
        "focus": "166,132,94",
    },
}

PAGE_VARIANTS = {
    "password_reset": {
        "type": "login", "theme": "security", "subtitle": "Account Security",
        "title": "Verifica attività recente",
        "description": "Ciao {target}, conferma l'identità per esaminare l'attività segnalata.",
        "action": "Verifica attività",
    },
    "repository_access": {
        "type": "login", "theme": "repository", "subtitle": "Repository Security",
        "title": "Revisione accesso repository",
        "description": "Ciao {target}, accedi per verificare la recente modifica ai permessi.",
        "action": "Verifica accesso",
    },
    "fake_invoice": {
        "type": "document", "theme": "invoice", "subtitle": "Billing Center",
        "title": "Fattura in attesa",
        "description": "Ciao {target}, un documento di fatturazione richiede la tua revisione.",
        "action": "Scarica dettagli fattura", "filename": "invoice-review.pdf",
    },
    "shared_document": {
        "type": "document", "theme": "document", "subtitle": "Document Workspace",
        "title": "Documento condiviso",
        "description": "Ciao {target}, è disponibile una nuova versione del documento condiviso.",
        "action": "Scarica documento", "filename": "shared-document.pdf",
    },
    "executive_impersonation": {
        "type": "approval", "theme": "management", "subtitle": "Management Office",
        "title": "Revisione richiesta",
        "description": "Ciao {target}, conferma di aver preso in carico il documento ricevuto.",
        "action": "Conferma revisione",
    },
    "event_invitation": {
        "type": "approval", "theme": "event", "subtitle": "Events Desk",
        "title": "Conferma invito",
        "description": "Ciao {target}, conferma la tua disponibilità per l'invito ricevuto.",
        "action": "Conferma disponibilità",
    },
}

DEFAULT_VARIANT = {
    "type": "approval", "theme": "default", "subtitle": "Secure Workspace",
    "title": "Richiesta di conferma",
    "description": "Ciao {target}, conferma di aver esaminato la richiesta ricevuta.",
    "action": "Conferma revisione",
}


def page_type(delivery: dict[str, str]) -> str:
    return _variant(delivery)["type"]


def landing_page(delivery: dict[str, str], tracking_id: str) -> str:
    variant = _variant(delivery)
    description = escape(
        variant["description"].format(target=delivery["target"])
    )
    header = f"""<div class="brand">MIRAGE</div>
<div class="subtitle">{escape(variant['subtitle'])}</div>
<h1>{escape(variant['title'])}</h1><p>{description}</p>"""

    if variant["type"] == "document":
        action = f"""<div class="document-card">
<div class="document-icon">PDF</div><div>
<div class="document-title">{escape(variant['filename'])}</div>
<div class="document-meta">Documento protetto · Anteprima non disponibile</div>
</div></div>
<a class="button" href="/download/{tracking_id}">{escape(variant['action'])}</a>
<p class="note">ⓘ Simulazione controllata: il file scaricato è innocuo e non raccoglie dati.</p>"""
    else:
        fields = ""
        if variant["type"] == "login":
            fields = """<label for="email">Email</label>
<input id="email" type="email" autocomplete="off" required>
<label for="password">Password</label>
<input id="password" type="password" autocomplete="new-password" required>"""
        action = f"""<form method="post" action="/submit/{tracking_id}" autocomplete="off">
{fields}<button type="submit">{escape(variant['action'])}</button></form>
<p class="note">ⓘ Simulazione controllata: nessun valore viene trasmesso o salvato.</p>"""

    return _page(
        f"Mirage - {variant['title']}",
        header + action,
        variant["theme"],
    )


def completion_page(delivery: dict[str, str]) -> str:
    variant = _variant(delivery)
    content = f"""<div class="brand">MIRAGE</div>
<div class="subtitle">{escape(variant['subtitle'])}</div>
<h1>Simulazione completata</h1>
<p>Hai completato una simulazione controllata realizzata nell'ambito del progetto
di tesi di Mario Lezzi.</p>
<p>Non sono state raccolte credenziali né altri dati personali.</p>"""
    return _page("Mirage - Simulazione completata", content, variant["theme"])


def _page(title: str, content: str, theme_name: str) -> str:
    theme = THEMES.get(theme_name, THEMES["default"])
    variables = ";".join(f"--{name}:{value}" for name, value in theme.items())
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{STYLE}</style></head>
<body style="{variables}"><main>{content}</main></body></html>"""


def _variant(delivery: dict[str, str]) -> dict[str, str]:
    return PAGE_VARIANTS.get(delivery.get("scenario", ""), DEFAULT_VARIANT)