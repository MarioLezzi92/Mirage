import json
import re
from pathlib import Path

from pydantic import BaseModel


class JsonWriter:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)

    def save(
        self,
        name: str,
        folder: str,
        data: BaseModel,
        *,
        suffix: str | None = None,
        omit_empty: bool = False,
    ) -> Path:
        directory = self.data_dir / folder
        directory.mkdir(parents=True, exist_ok=True)
        slug = self._slug(name)
        suffix = suffix or folder
        sequence = self._next_sequence(directory, slug, suffix)
        path = directory / f"{slug}-{suffix}-{sequence}.json"
        payload = data.model_dump(
            mode="json",
            exclude_none=omit_empty,
            exclude_defaults=omit_empty,
        )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "target"

    @staticmethod
    def _next_sequence(directory: Path, slug: str, kind: str) -> int:
        pattern = re.compile(rf"^{re.escape(slug)}-{re.escape(kind)}-(\d+)\.json$")
        numbers = [
            int(match.group(1))
            for path in directory.glob(f"{slug}-{kind}-*.json")
            if (match := pattern.match(path.name))
        ]
        return max(numbers, default=0) + 1
