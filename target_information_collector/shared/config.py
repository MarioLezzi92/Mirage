import os
from pathlib import Path

from pydantic import BaseModel, Field


DEFAULT_SOCIAL_DISCOVERY_ACTOR_ID = "tri_angle~social-media-finder"
DEFAULT_FACEBOOK_SEARCH_ACTOR_ID = "memo23~facebook-search-scraper"


class Settings(BaseModel):
    github_token: str | None = None
    apify_token: str | None = None
    apify_search_actor_id: str | None = None
    apify_social_discovery_actor_id: str | None = (
        DEFAULT_SOCIAL_DISCOVERY_ACTOR_ID
    )
    apify_facebook_search_actor_id: str | None = DEFAULT_FACEBOOK_SEARCH_ACTOR_ID
    apify_linkedin_actor_id: str | None = None
    apify_instagram_actor_id: str | None = None
    apify_facebook_actor_id: str | None = None
    search_country_code: str = "it"
    identity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_candidates_per_source: int = Field(default=4, ge=1, le=20)
    output_dir: str = "target_information_collector/data"

    @classmethod
    def from_environment(cls, env_file: str | Path = ".env") -> "Settings":
        values = {
            key.upper(): value
            for key, value in cls._read_env_file(Path(env_file)).items()
        }
        values.update({key.upper(): value for key, value in os.environ.items()})

        def first(*names: str) -> str | None:
            for name in names:
                value = values.get(name)
                if value is not None and value.strip():
                    return value.strip()
            return None

        return cls(
            github_token=first("GITHUB_TOKEN"),
            apify_token=first("APIFY_TOKEN"),
            apify_search_actor_id=first("APIFY_SEARCH_ACTOR_ID", "APIFY_ACTOR_ID"),
            apify_social_discovery_actor_id=first(
                "APIFY_SOCIAL_DISCOVERY_ACTOR_ID",
                "APIFY_SOCIAL_MEDIA_FINDER_ACTOR_ID",
            )
            or DEFAULT_SOCIAL_DISCOVERY_ACTOR_ID,
            apify_facebook_search_actor_id=first(
                "APIFY_FACEBOOK_SEARCH_ACTOR_ID"
            )
            or DEFAULT_FACEBOOK_SEARCH_ACTOR_ID,
            apify_linkedin_actor_id=first(
                "APIFY_LINKEDIN_ACTOR_ID",
                "APIFY_LINKEDIN_PROFILE_ACTOR_ID",
            ),
            apify_instagram_actor_id=first(
                "APIFY_INSTAGRAM_ACTOR_ID",
                "APIFY_INSTAGRAM_PROFILE_ACTOR_ID",
            ),
            apify_facebook_actor_id=first(
                "APIFY_FACEBOOK_ACTOR_ID",
                "APIFY_FACEBOOK_PROFILE_ACTOR_ID",
            ),
            search_country_code=first("SEARCH_COUNTRY_CODE") or "it",
            identity_threshold=float(first("IDENTITY_THRESHOLD") or "0.7"),
            max_candidates_per_source=int(first("MAX_CANDIDATES_PER_SOURCE") or "4"),
            output_dir=first("TARGET_OUTPUT_DIR")
            or "target_information_collector/data",
        )

    @staticmethod
    def _read_env_file(path: Path) -> dict[str, str]:
        if not path.exists():
            return {}
        values: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values
