import json
import re
from pathlib import Path


class JsonProfileWriter:
    def __init__(self):
        # Punta alla cartella raw dentro data
        self.raw_dir = Path(__file__).resolve().parent.parent / "data" / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _slugify(self, name: str) -> str:
        return name.lower().replace(" ", "-")

    def _get_next_id(self, slug: str) -> int:
        # Conta i file esistenti per calcolare il prossimo ID incrementale
        files = list(self.raw_dir.glob(f"{slug}-raw-*.json"))
        if not files:
            return 1
        
        highest_id = 0
        pattern = re.compile(rf"{slug}-raw-(\d+)\.json")
        for file in files:
            match = pattern.search(file.name)
            if match:
                current_id = int(match.group(1))
                if current_id > highest_id:
                    highest_id = current_id
        return highest_id + 1

    def save(self, name: str, data: dict) -> str:
        """Salva il dizionario di dati grezzi in data/raw."""
        slug = self._slugify(name)
        filename_slug = f"{slug}-raw"
            
        new_id = self._get_next_id(slug)
        filename = f"{filename_slug}-{new_id}.json"
        file_path = self.raw_dir / filename
        
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        return filename