import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from target_information_collector.agents.github_agent import GitHubAgent
from target_information_collector.agents.input_agent import InputAgent
from target_information_collector.agents.social_agent import SocialAgent
from target_information_collector.agents.social_discovery_agent import (
    SocialDiscoveryAgent,
)
from target_information_collector.agents.web_agent import WebAgent
from target_information_collector.core.collector_pipeline import CollectorPipeline
from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.core.profile_builder import ProfileBuilder
from target_information_collector.providers.apify_provider import (
    ApifyActor,
    ApifyFacebookSearchProvider,
    ApifySearchProvider,
    ApifySocialDiscoveryProvider,
    ApifySocialProvider,
    apify_usage,
)
from target_information_collector.providers.github_provider import GitHubProvider
from target_information_collector.providers.http_client import HttpClient
from target_information_collector.shared.config import Settings
from target_information_collector.shared.models import TargetInput, TargetProfile
from target_information_collector.storage.json_writer import JsonWriter


@dataclass(frozen=True)
class SavedResult:
    raw_file: Path
    profile_file: Path
    profile: TargetProfile
    errors: list[str]
    warnings: list[str]
    active_profile_agents: list[str]


class TargetInformationService:
    def __init__(self, pipeline: CollectorPipeline, writer: JsonWriter) -> None:
        self.pipeline = pipeline
        self.writer = writer
        self.builder = ProfileBuilder()

    def collect(
        self,
        target: TargetInput,
        progress: Callable[[str, int, int, str], None] | None = None,
    ) -> SavedResult:
        raw = self.pipeline.collect(target, progress)
        profile = self.builder.build(raw)
        return SavedResult(
            raw_file=self.writer.save(target.full_name, "raw", raw),
            profile_file=self.writer.save(
                target.full_name,
                "profiles",
                profile,
                suffix="structured",
                omit_empty=True,
            ),
            profile=profile,
            errors=raw.errors,
            warnings=raw.warnings,
            active_profile_agents=raw.active_profile_agents,
        )


def build_service(settings: Settings) -> TargetInformationService:
    client, matcher = HttpClient(), IdentityMatcher()
    github = GitHubAgent(
        GitHubProvider(client, settings.github_token),
        matcher,
        settings.identity_threshold,
        settings.max_candidates_per_source,
    )
    discovery, profiles = [InputAgent(), github], {"github": github}

    def actor(actor_id: str) -> ApifyActor:
        return ApifyActor(client, settings.apify_token or "", actor_id)

    if settings.apify_token and settings.apify_search_actor_id:
        discovery.append(
            WebAgent(
                ApifySearchProvider(
                    actor(settings.apify_search_actor_id),
                    settings.search_country_code,
                ),
                matcher,
                settings.max_candidates_per_source,
            )
        )
    if settings.apify_token and settings.apify_social_discovery_actor_id:
        discovery.append(
            SocialDiscoveryAgent(
                ApifySocialDiscoveryProvider(
                    actor(settings.apify_social_discovery_actor_id)
                ),
                settings.max_candidates_per_source,
            )
        )
    if settings.apify_token and settings.apify_facebook_search_actor_id:
        discovery.append(
            WebAgent(
                ApifyFacebookSearchProvider(
                    actor(settings.apify_facebook_search_actor_id)
                ),
                matcher,
                max(settings.max_candidates_per_source, 10),
                name="facebook_search",
            )
        )

    actor_ids = {
        "linkedin": settings.apify_linkedin_actor_id,
        "instagram": settings.apify_instagram_actor_id,
        "facebook": settings.apify_facebook_actor_id,
    }
    for platform, actor_id in actor_ids.items():
        if settings.apify_token and actor_id:
            profiles[platform] = SocialAgent(
                platform,
                ApifySocialProvider(platform, actor(actor_id)),
                matcher,
                settings.identity_threshold,
            )

    return TargetInformationService(
        CollectorPipeline(discovery, profiles),
        JsonWriter(settings.output_dir),
    )


def collect_file(
    input_file: str | Path | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> list[SavedResult]:
    settings = Settings.from_environment()
    path = Path(input_file or os.getenv("TARGET_INPUT_FILE", "target_input.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    service = build_service(settings)
    return [
        service.collect(TargetInput.model_validate(item), progress)
        for item in (data if isinstance(data, list) else [data])
    ]


def run(input_file: str | Path | None = None) -> None:
    settings = Settings.from_environment()
    usage_client = HttpClient()
    usage_before: tuple[float, float] | None = None
    if settings.apify_token:
        try:
            usage_before = apify_usage(usage_client, settings.apify_token)
            used, limit = usage_before
            remaining = max(0.0, limit - used)
            print(
                f"Apify iniziale: ${remaining:.2f} rimanenti | "
                f"${used:.2f}/${limit:.2f} usati"
            )
        except Exception:
            print("Apify iniziale: utilizzo non disponibile")

    try:
        for result in collect_file(input_file, _terminal_progress):
            print(f"raw: {result.raw_file}\nprofile: {result.profile_file}")
            for error in result.errors:
                print(f"error: {error}")
    finally:
        if settings.apify_token:
            try:
                used, limit = apify_usage(usage_client, settings.apify_token)
                remaining = max(0.0, limit - used)
                if usage_before:
                    spent = max(0.0, used - usage_before[0])
                    print(
                        f"Apify run: ${spent:.4f} spesi | "
                        f"${remaining:.2f} rimanenti"
                    )
                else:
                    print(
                        "Apify run: spesa non disponibile | "
                        f"${remaining:.2f} rimanenti"
                    )
            except Exception:
                print("Apify fine run: utilizzo non disponibile")


def _terminal_progress(phase: str, done: int, total: int, label: str) -> None:
    width = 24
    ratio = 1.0 if total == 0 else done / total
    filled = round(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    status = "completato" if done >= total else label
    line = f"\r{phase:<8} [{bar}] {done:>2}/{total:<2} {status[:42]:<42}"
    print(line, end="\n" if done >= total else "", flush=True)
