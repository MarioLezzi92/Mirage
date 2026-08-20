import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


STOPWORDS = {
    "a", "and", "azienda", "company", "da", "degli", "dei", "del", "della",
    "delle", "di", "inc", "ltd", "of", "spa", "srl", "studi", "the",
    "universita", "university",
}


def normalize(value: str | None) -> str:
    if not value:
        return ""
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text.casefold()))


def tokens(value: str | None) -> set[str]:
    return {part for part in normalize(value).split() if len(part) > 1 and part not in STOPWORDS}


def platform_from_url(url: str) -> str:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    for platform in ("linkedin", "github", "facebook", "instagram"):
        if host == f"{platform}.com" or host.endswith(f".{platform}.com"):
            return platform
    return "web"


def profile_username(url: str, platform: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "linkedin" and len(parts) >= 2 and parts[0].casefold() == "in":
        return parts[1]
    if platform == "facebook" and parsed.path.casefold() == "/profile.php":
        return (parse_qs(parsed.query).get("id") or [None])[0]
    if (
        platform == "facebook"
        and len(parts) >= 3
        and parts[0].casefold() == "people"
    ):
        return parts[-1]
    if platform in {"github", "instagram", "facebook"} and len(parts) == 1:
        return parts[0]
    return None


def is_profile_url(url: str, platform: str) -> bool:
    username = profile_username(url, platform)
    if not username:
        return False
    blocked = {
        "about", "company", "events", "explore", "groups", "login", "p",
        "posts", "reel", "reels", "search", "share", "stories", "watch",
    }
    return username.casefold() not in blocked


def owner_name_matches(full_name: str, title: str) -> bool:
    """Il nome deve identificare il proprietario, non una persona citata dopo."""
    expected = normalize(full_name)
    reversed_name = " ".join(reversed(expected.split()))
    title_normalized = normalize(title)
    return any(
        title_normalized == candidate
        or title_normalized.startswith(f"{candidate} ")
        for candidate in (expected, reversed_name)
    )


def profile_owner_matches(
    full_name: str,
    title: str = "",
    username: str | None = None,
) -> bool:
    """Il risultato deve rappresentare la persona, non una pagina che la cita."""
    return owner_name_matches(full_name, title) or owner_name_matches(
        full_name,
        username or "",
    )


def email_owner_matches(full_name: str, email: str) -> bool:
    """Accetta dal web solo indirizzi il cui local-part identifica il target."""
    parts = normalize(full_name).split()
    if len(parts) < 2 or "@" not in email:
        return False

    first, last = parts[0], parts[-1]
    local = normalize(email.split("@", 1)[0]).replace(" ", "")
    local = re.sub(r"\d+$", "", local)
    variants = {
        f"{first}{last}",
        f"{last}{first}",
        f"{first[0]}{last}",
        f"{last}{first[0]}",
        f"{first}{last[0]}",
        f"{last[0]}{first}",
    }
    return local in variants


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    for prefix in ("www.", "m.", "it.", "at.", "ar."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    path = parsed.path.rstrip("/")
    query = ""
    if host == "facebook.com" and path.casefold() == "/profile.php":
        profile_id = (parse_qs(parsed.query).get("id") or [None])[0]
        if profile_id:
            query = urlencode({"id": profile_id})
    return urlunparse(("https", host, path, "", query, ""))
