# Phishing Campaign Generator

Modulo 2 della pipeline.

Riceve il profilo strutturato generato dal `Target Information Collector`, seleziona il template di campagna più adatto in base ai dati del target e produce un payload pulito per il modulo NLP.

Il modulo non usa AI e non genera la mail finale.

## File

- `models.py`: modelli Pydantic di input/output.
- `service.py`: logica di selezione del template.
- `templates.json`: catalogo dei template disponibili.
- `data/`: payload generati.

## Output

Il payload prodotto contiene:

- `target`: dati essenziali del target.
- `campaign`: template selezionato, scenario, categoria e vincoli di sicurezza.

