import json
from pathlib import Path

from phishing_campaign_generator.models import TargetProfile
from phishing_campaign_generator.service import CampaignGeneratorService
from target_information_collector.core.target_information_service import TargetInformationService
from target_information_collector.shared.models import TargetInput


TARGET_INPUT_FILE = "target_input.json"

USE_MOCK_RAW = False  # Imposta a True per testare con un file raw locale invece di raccogliere dati dal vivo
MOCK_RAW_FILE = "gerardo-leone-raw-1.json"


def read_json(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def find_file(filename: str, root: str) -> Path:
    for path in Path(root).rglob(filename):
        if path.is_file():
            return path

    raise FileNotFoundError(f"File non trovato: {filename}")


def load_mock_raw(filename: str) -> dict:
    raw_path = (
        Path("target_information_collector")
        / "data"
        / "raw"
        / filename
    )

    return read_json(raw_path)


def main() -> None:
    print("\n=== SocialEng Local Pipeline ===")

    target_data = read_json(TARGET_INPUT_FILE)
    target = TargetInput(**target_data)

    print("\n[1/2] Avvio Target Information Collector...")

    collector = TargetInformationService()

    if USE_MOCK_RAW:
        print(f"MODALITÀ TEST: caricamento raw locale da {MOCK_RAW_FILE}")

        raw_data = load_mock_raw(MOCK_RAW_FILE)

        collector_result = collector.collect_from_raw(
            target=target,
            raw_data=raw_data,
            raw_filename=MOCK_RAW_FILE,
        )
    else:
        print("MODALITÀ LIVE: avvio raccolta dati dal vivo")

        collector_result = collector.collect_live(target)

    structured_file = collector_result["structured_file"]
    structured_path = find_file(
        filename=structured_file,
        root="target_information_collector",
    )

    print(f"[OK] Profilo strutturato generato: {structured_path}")

    print("\n[2/2] Avvio Phishing Campaign Generator...")

    profile_data = read_json(structured_path)
    profile = TargetProfile(**profile_data)

    campaign_service = CampaignGeneratorService()
    campaign_payload = campaign_service.generate_payload(profile)

    payload_path = (
        Path("phishing_campaign_generator")
        / "data"
        / f"payload_{campaign_payload.target.name.replace(' ', '_')}.json"
    )

    print(f"[OK] Campaign payload generato: {payload_path}")

    print("\n=== Pipeline completata ===")


if __name__ == "__main__":
    main()