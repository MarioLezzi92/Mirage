import base64
from binascii import Error as Base64Error
from typing import Any
from urllib.error import HTTPError

from target_information_collector.providers.http_client import HttpClient


class GitHubProvider:
    base_url = "https://api.github.com"

    def __init__(self, client: HttpClient, token: str | None = None) -> None:
        self.client = client
        self.token = token

    def search_users(self, full_name: str, limit: int = 5) -> list[dict[str, Any]]:
        data = self.client.get_json(
            f"{self.base_url}/search/users",
            params={"q": f'"{full_name}" in:fullname', "per_page": limit},
            headers=self._headers(),
        )
        return data.get("items", []) if isinstance(data, dict) else []

    def get_user(self, username: str) -> dict[str, Any]:
        data = self.client.get_json(
            f"{self.base_url}/users/{username}",
            headers=self._headers(),
        )
        if not isinstance(data, dict):
            raise ValueError("Risposta profilo GitHub non valida")
        return data

    def get_profile_readme(self, username: str) -> str:
        try:
            data = self.client.get_json(
                f"{self.base_url}/repos/{username}/{username}/readme",
                headers=self._headers(),
            )
        except HTTPError as exc:
            if exc.code == 404:
                return ""
            raise

        if not isinstance(data, dict):
            return ""
        content = data.get("content")
        if data.get("encoding") != "base64" or not isinstance(content, str):
            return ""
        try:
            return base64.b64decode(content).decode("utf-8", errors="ignore")
        except (Base64Error, ValueError):
            return ""

    def get_social_accounts(self, username: str) -> list[dict[str, Any]]:
        data = self.client.get_json(
            f"{self.base_url}/users/{username}/social_accounts",
            params={"per_page": 100},
            headers=self._headers(),
        )
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def get_repositories(self, username: str) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.client.get_json(
                f"{self.base_url}/users/{username}/repos",
                params={"per_page": 100, "page": page, "sort": "updated"},
                headers=self._headers(),
            )
            if not isinstance(data, list):
                return repositories
            repositories.extend(item for item in data if isinstance(item, dict))
            if len(data) < 100:
                return repositories
            page += 1

    def get_repository_languages(
        self,
        repository: dict[str, Any],
    ) -> dict[str, int]:
        url = repository.get("languages_url")
        if not url and repository.get("full_name"):
            url = f"{self.base_url}/repos/{repository['full_name']}/languages"
        if not url:
            return {}

        data = self.client.get_json(url, headers=self._headers())
        if not isinstance(data, dict):
            return {}
        return {
            str(language): int(size)
            for language, size in data.items()
            if isinstance(size, (int, float)) and size > 0
        }

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers
