import re
from urllib.parse import urlparse, urlunparse


class EvidenceNormalizer:
    SOCIAL_BAD_PATHS = {
        "groups",
        "posts",
        "post",
        "reel",
        "reels",
        "p",
        "tv",
        "public",
        "pages",
        "people",
        "albums",
        "photos",
        "photo",
        "watch",
        "events",
        "marketplace",
        "stories",
        "story",
        "hashtag",
        "explore",
        "share",
        "sharer",
        "search",
    }

    BLOCKED_DOMAINS = {
        "fastbackgroundcheck.com",
        "truepeoplesearch.com",
        "whitepages.com",
        "spokeo.com",
        "beenverified.com",
        "peoplefinders.com",
        "radaris.com",
        "mylife.com",
        "nationalpublicdata.com",
        "anywho.com",
        "rocketreach.co",
        "ourstates.org",
        "paginebianche.it",
    }

    BLOCKED_PATTERNS = [
        "/pub/dir/",
        "/directory/people/",
        "/search/",
        "tiktok.com/@",
        "ibs.it/",
        "dokumen.pub/",
    ]

    TECH_TERMS = {
        "cybersecurity": "Cybersecurity",
        "cyber security": "Cyber Security",
        "information security": "Information Security",
        "penetration testing": "Penetration Testing",
        "ethical hacking": "Ethical Hacking",
        "vulnhub": "VulnHub",
        "tryhackme": "TryHackMe",
        "hackthebox": "Hack The Box",
        "machine learning": "Machine Learning",
        "deep learning": "Deep Learning",
        "artificial intelligence": "Artificial Intelligence",
        "python": "Python",
        "java": "Java",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "html": "HTML",
        "css": "CSS",
        "php": "PHP",
        "spring": "Spring",
        "spring boot": "Spring Boot",
        "flutter": "Flutter",
        "dart": "Dart",
        "react": "React",
        "angular": "Angular",
        "vue": "Vue",
        "sql": "SQL",
        "postgresql": "PostgreSQL",
        "mysql": "MySQL",
        "docker": "Docker",
        "kubernetes": "Kubernetes",
        "azure": "Azure",
        "aws": "AWS",
        "linux": "Linux",
        "jupyter notebook": "Jupyter Notebook",
    }

    def normalize_url(self, url: str | None) -> str | None:
        if not url:
            return None

        parsed = urlparse(url.strip())

        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()

        if netloc in {
            "www.facebook.com",
            "web.facebook.com",
            "m.facebook.com",
            "mbasic.facebook.com",
        }:
            netloc = "facebook.com"
        elif netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query = parsed.query if path.lower().endswith("profile.php") else ""

        return urlunparse((scheme, netloc, path, "", query, ""))

    def is_blocked_url(self, url: str | None) -> bool:
        if not url:
            return True

        parsed = urlparse(url.lower())
        domain = parsed.netloc.lower()
        full_url = url.lower()

        if any(blocked in domain for blocked in self.BLOCKED_DOMAINS):
            return True

        if any(pattern in full_url for pattern in self.BLOCKED_PATTERNS):
            return True

        return False

    def detect_platform(self, url: str | None) -> str | None:
        if not url:
            return None

        domain = urlparse(url).netloc.lower()

        if "github.com" in domain:
            return "github"

        if "linkedin.com" in domain:
            return "linkedin"

        if "facebook.com" in domain:
            return "facebook"

        if "instagram.com" in domain:
            return "instagram"

        if self.is_institutional_domain(domain):
            return "institutional"

        return "web"

    def classify_url(self, url: str | None) -> str:
        if not url:
            return "unknown"

        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()

        if "linkedin.com" in domain:
            return "professional_profile" if "/in/" in path else "web_mention"

        if "github.com" in domain:
            parts = [part for part in parsed.path.split("/") if part.strip()]
            return "technical_profile" if len(parts) == 1 else "web_mention"

        if "facebook.com" in domain or "instagram.com" in domain:
            return "social_profile_candidate" if self.is_social_profile_url(url) else "social_contextual_mention"

        if self.is_institutional_domain(domain):
            return "institutional_reference"

        return "web_mention"

    def extract_username(self, url: str | None) -> str | None:
        if not url:
            return None

        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        parts = [part for part in parsed.path.split("/") if part.strip()]

        if not parts:
            return None

        if "linkedin.com" in domain:
            if len(parts) >= 2 and parts[0].lower() == "in":
                return parts[1]
            return None

        if "facebook.com" in domain and parts[0].lower() == "profile.php":
            match = re.search(r"(?:^|&)id=([^&]+)", parsed.query)
            return match.group(1) if match else None

        return parts[0]

    def is_social_profile_url(self, url: str | None) -> bool:
        if not url:
            return False

        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        parts = [part.lower() for part in parsed.path.split("/") if part.strip()]

        if not parts:
            return False

        if parts[0] in self.SOCIAL_BAD_PATHS:
            return False

        if "instagram.com" in domain:
            return len(parts) == 1

        if "facebook.com" in domain:
            return len(parts) <= 2

        return False

    def is_institutional_domain(self, domain: str) -> bool:
        return any(
            marker in domain
            for marker in [
                ".edu",
                ".ac.",
                ".gov",
                ".org",
                "universit",
                "university",
            ]
        )

    def extract_emails(self, text: str) -> list[str]:
        if not text:
            return []

        emails = re.findall(
            r"(?<![a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
            text,
        )

        cleaned = []

        for email in emails:
            value = email.strip(".,;:()[]{}<>").lower()

            if len(value.split("@")[0]) >= 3:
                cleaned.append(value)

        return self.unique(cleaned)

    def extract_tech_stack_terms(self, text: str) -> list[str]:
        if not text:
            return []

        lower = text.lower()
        values = []

        for key, value in self.TECH_TERMS.items():
            if key in lower:
                values.append(value)

        return self.unique(values)

    def unique(self, values: list[str]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            if not value:
                continue

            cleaned = str(value).strip()

            if not cleaned:
                continue

            key = cleaned.lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(cleaned)

        return output