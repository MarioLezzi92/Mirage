import re
from typing import Any
from urllib.parse import quote

from target_information_collector.providers.http_client import HttpClient
from target_information_collector.shared.models import SearchResult
from target_information_collector.shared.text import platform_from_url


class ProviderLimitError(RuntimeError):
    stop_platform = True


def apify_usage(client: HttpClient, token: str) -> tuple[float, float]:
    data = client.get_json(
        "https://api.apify.com/v2/users/me/limits",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = data.get("data", {}) if isinstance(data, dict) else {}
    used = payload.get("current", {}).get("monthlyUsageUsd")
    limit = payload.get("limits", {}).get("maxMonthlyUsageUsd")
    if not isinstance(used, (int, float)) or not isinstance(limit, (int, float)):
        raise ValueError("Risposta utilizzo Apify non valida")
    return float(used), float(limit)


class ApifyActor:
    base_url = "https://api.apify.com/v2/actors"

    def __init__(self, client: HttpClient, token: str, actor_id: str) -> None:
        self.client = client
        self.token = token
        self.actor_id = actor_id

    def run(self, payload: dict[str, Any], timeout: int = 180) -> list[dict[str, Any]]:
        url = (
            f"{self.base_url}/{quote(self.actor_id, safe='~')}/"
            f"run-sync-get-dataset-items?token={quote(self.token)}"
        )
        data = self.client.post_json(url, payload, timeout=timeout)
        if not isinstance(data, list):
            raise ValueError("L'Actor Apify non ha restituito una lista")
        items = [item for item in data if isinstance(item, dict)]
        for item in items:
            message = item.get("message")
            if isinstance(message, str) and any(
                marker in message.casefold()
                for marker in (
                    "limit of",
                    "limit reached",
                    "quota exceeded",
                    "upgrade your apify plan",
                )
            ):
                raise ProviderLimitError(message)
        return items


class ApifySearchProvider:
    def __init__(self, actor: ApifyActor, country_code: str = "it") -> None:
        self.actor = actor
        self.country_code = country_code

    def search(self, queries: list[str]) -> list[SearchResult]:
        rows = self.actor.run(
            {
                "queries": "\n".join(queries),
                "resultsPerPage": 10,
                "countryCode": self.country_code,
            }
        )
        results: list[SearchResult] = []
        for row in rows:
            search_query = row.get("searchQuery") or row.get("search_query") or {}
            query = (
                search_query.get("term")
                if isinstance(search_query, dict)
                else str(search_query or "")
            ) or row.get("query") or ""
            organic = row.get("organicResults") or row.get("organic_results") or []
            for item in organic:
                url = item.get("url") or item.get("link")
                if not url:
                    continue
                results.append(
                    SearchResult(
                        url=url,
                        title=item.get("title") or "",
                        snippet=item.get("description") or item.get("snippet") or "",
                        query=str(query),
                    )
                )
        return results


class ApifyFacebookSearchProvider:
    """Ricerca persone nella ricerca pubblica nativa di Facebook."""

    def __init__(self, actor: ApifyActor) -> None:
        self.actor = actor

    def search(self, queries: list[str]) -> list[SearchResult]:
        plain_queries: list[str] = []
        handle_queries: list[str] = []
        for query in queries:
            lowered = query.casefold()
            if "site:facebook.com" in lowered and "inurl:" in lowered:
                match = re.search(r"\binurl:([^\s]+)", query, re.IGNORECASE)
                if match:
                    handle = match.group(1).strip('"\'')
                    if handle and handle not in handle_queries:
                        handle_queries.append(handle)
                continue
            if "site:" in lowered or "inurl:" in lowered:
                continue

            value = query.replace('"', "").strip()
            if value and value not in plain_queries:
                plain_queries.append(value)

        # Su Facebook gli handle puntano meglio dei soli nomi in presenza di
        # molti omonimi. La forma con il punto è la più comune per i profili.
        handle_queries.sort(
            key=lambda value: (
                0 if "." in value else 1 if "_" in value else 2,
                len(value),
            )
        )
        contextual = max(plain_queries, key=len, default=None)
        generic = min(plain_queries, key=len, default=None)
        clean_queries = [*handle_queries[:3]]
        for value in (contextual, generic):
            if value and value not in clean_queries:
                clean_queries.append(value)

        rows = self.actor.run(
            {
                "searchQueries": clean_queries,
                "searchType": "people",
                "maxItems": 100,
            }
        )
        results: list[SearchResult] = []
        for row in rows:
            row_type = str(row.get("rowType") or row.get("type") or "person")
            if row_type.casefold() not in {"person", "people", "profile", "user"}:
                continue
            url = row.get("url") or row.get("profileUrl") or row.get("profile_url")
            if not url or platform_from_url(str(url)) != "facebook":
                continue
            user_data = row.get("userData") or row.get("user_data") or []
            details = " ".join(
                str(item.get("text") or "")
                for item in user_data
                if isinstance(item, dict)
            )
            results.append(
                SearchResult(
                    url=url,
                    title=row.get("name") or row.get("profileName") or "",
                    snippet=row.get("subtitle") or row.get("description") or details,
                )
            )
        return results


class ApifySocialDiscoveryProvider:
    """Adatta il Social Media Finder Apify all'interfaccia di discovery."""

    _SOCIAL_URL = re.compile(
        r"(?:https?://)?(?:www\.)?"
        r"(?:linkedin\.com/in|instagram\.com|facebook\.com)/"
        r"[^\s\"'<>]+",
        re.IGNORECASE,
    )

    def __init__(self, actor: ApifyActor) -> None:
        self.actor = actor

    def find_profiles(
        self,
        profile_names: list[str],
        platforms: list[str],
    ) -> list[str]:
        rows = self.actor.run(
            {
                "profileNames": profile_names,
                "socials": platforms,
            }
        )
        allowed = set(platforms)
        urls: list[str] = []
        seen: set[str] = set()
        for value in self._strings(rows):
            for match in self._SOCIAL_URL.finditer(value):
                url = match.group(0).rstrip(".,;:!?)]}")
                if not url.casefold().startswith(("http://", "https://")):
                    url = f"https://{url}"
                if platform_from_url(url) not in allowed:
                    continue
                key = url.casefold()
                if key not in seen:
                    urls.append(url)
                    seen.add(key)
        return urls

    @classmethod
    def _strings(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            output: list[str] = []
            for item in value.values():
                output.extend(cls._strings(item))
            return output
        if isinstance(value, list):
            output = []
            for item in value:
                output.extend(cls._strings(item))
            return output
        return []


class ApifySocialProvider:
    def __init__(self, platform: str, actor: ApifyActor) -> None:
        self.platform = platform
        self.actor = actor

    def fetch(self, url: str, username: str | None) -> list[dict[str, Any]]:
        if self.platform == "linkedin":
            payload = {"includeEmail": True, "username": username or url}
        elif self.platform == "instagram":
            payload = {"usernames": [username]} if username else {"directUrls": [url]}
        elif self.platform == "facebook":
            payload = {"endpoint": "details_by_url", "max_posts": 0, "urls_text": url}
        else:
            raise ValueError(f"Piattaforma Apify non supportata: {self.platform}")
        rows = self.actor.run(payload)
        return (
            [self._linkedin_row(row, username) for row in rows]
            if self.platform == "linkedin"
            else rows
        )

    @staticmethod
    def _linkedin_row(
        row: dict[str, Any],
        username: str | None,
    ) -> dict[str, Any]:
        basic = row.get("basic_info")
        if not isinstance(basic, dict):
            return row
        location = basic.get("location")
        if isinstance(location, dict):
            location = location.get("full") or location.get("city")
        return {
            **row,
            "fullName": basic.get("fullname"),
            "username": basic.get("public_identifier") or username,
            "headline": basic.get("headline"),
            "about": basic.get("about"),
            "company": basic.get("current_company"),
            "location": location,
            "email": basic.get("email"),
            "skills": basic.get("top_skills"),
        }
