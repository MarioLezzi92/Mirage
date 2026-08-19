import re

from target_information_collector.shared.models import ProfileData, TargetInput
from target_information_collector.shared.text import normalize, tokens


class IdentityMatcher:
    """Verifica gli omonimi con regole deterministiche e spiegabili."""

    def score(
        self,
        target: TargetInput,
        profile: ProfileData,
        *,
        discovery_context: str = "",
        explicit: bool = False,
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []
        if explicit:
            return 1.0, ["profilo fornito esplicitamente"]

        name_matches = self._same_name(target.full_name, profile.full_name)
        username_matches = self._same_username(
            target.full_name,
            profile.username,
        )
        if not name_matches and not username_matches:
            return 0.0, ["nome non corrispondente"]

        score = 0.6 if username_matches else 0.5
        if name_matches:
            reasons.append("nome completo corrispondente")
        if username_matches:
            reasons.append("username compatibile con il nome")
        context = " ".join(
            value
            for value in (
                profile.company,
                profile.role,
                profile.bio,
                *profile.locations,
                *profile.education,
                discovery_context,
            )
            if value
        )

        company_context = " ".join(
            value
            for value in (
                profile.role,
                profile.bio,
                *profile.locations,
                *profile.education,
                discovery_context,
            )
            if value
        )
        company_matches = bool(
            target.company
            and profile.company
            and self._organization_overlap(target.company, profile.company)
        )
        contextual_company_matches = bool(
            target.company
            and self._organization_overlap(target.company, company_context)
            and not self._conflicting_affiliation(
                target.company,
                " ".join(
                    value
                    for value in (profile.role, discovery_context)
                    if value
                ),
            )
        )
        if company_matches or contextual_company_matches:
            score += 0.2
            reasons.append("organizzazione compatibile")
        if target.role and self._overlap(target.role, context):
            score += 0.1
            reasons.append("ruolo compatibile")
        if target.cities and any(
            self._location_overlap(city, context) for city in target.cities
        ):
            # Handle coerente + stessa città già verificata è un segnale più
            # forte del solo nome, soprattutto tra omonimi social.
            score += 0.2 if username_matches else 0.1
            reasons.append("località compatibile")
        if target.education and any(
            self._overlap(item, context) for item in target.education
        ):
            score += 0.1
            reasons.append("formazione compatibile")

        return min(score, 1.0), reasons

    def score_text(self, target: TargetInput, text: str) -> tuple[float, list[str]]:
        normalized_text = normalize(text)
        if normalize(target.full_name) not in normalized_text:
            reversed_name = " ".join(reversed(normalize(target.full_name).split()))
            if reversed_name not in normalized_text:
                return 0.0, ["nome assente dalla menzione"]

        score = 0.6
        reasons = ["nome presente nella menzione"]
        for label, values, weight in (
            ("organizzazione", [target.company] if target.company else [], 0.2),
            ("ruolo", [target.role] if target.role else [], 0.1),
            ("località", target.cities, 0.1),
            ("formazione", target.education, 0.1),
        ):
            matches = (
                self._organization_overlap
                if label == "organizzazione"
                else self._overlap
            )
            if any(value and matches(value, text) for value in values):
                score += weight
                reasons.append(f"{label} compatibile")
        return min(score, 1.0), reasons

    @staticmethod
    def _same_name(expected: str, actual: str | None) -> bool:
        if not actual:
            return False
        left = normalize(expected)
        right = normalize(actual)
        return left == right or left == " ".join(reversed(right.split()))

    @staticmethod
    def _same_username(expected: str, username: str | None) -> bool:
        if not username:
            return False
        parts = normalize(expected).split()
        if len(parts) < 2:
            return False

        compact = "".join(normalize(username).split())
        variants = (
            f"{parts[0]}{parts[-1]}",
            f"{parts[-1]}{parts[0]}",
        )
        for variant in variants:
            suffix = compact.removeprefix(variant)
            if compact == variant or (compact.startswith(variant) and suffix.isdigit()):
                return True
        return False

    @staticmethod
    def _overlap(expected: str, actual: str) -> bool:
        expected_tokens = tokens(expected)
        actual_tokens = tokens(actual)
        if not expected_tokens or not actual_tokens:
            return False
        if expected_tokens & actual_tokens:
            return True
        return any(
            len(left) >= 5
            and len(right) >= 5
            and (left in right or right in left)
            for left in expected_tokens
            for right in actual_tokens
        )

    @classmethod
    def _organization_overlap(cls, expected: str, actual: str) -> bool:
        if cls._overlap(expected, actual):
            return True
        compact = normalize(actual).replace(" ", "")
        return any(
            alias in compact
            for alias in cls.organization_aliases(expected)
        )

    @staticmethod
    def organization_aliases(value: str) -> set[str]:
        """Ricava alias universitari brevi senza dizionari legati al target."""
        normalized = normalize(value)
        if not normalized.startswith(("universita ", "university ")):
            return set()
        return {
            f"uni{part[:2]}"
            for part in tokens(value)
            if len(part) >= 4
        }

    @classmethod
    def _conflicting_affiliation(cls, expected: str, value: str) -> bool:
        affiliations = re.findall(
            r"(?:@|\bworks\s+at\b|\blavora\s+presso\b|\bat\b|\bpresso\b)"
            r"\s+([^.;|·]+)",
            value,
            flags=re.IGNORECASE,
        )
        return bool(affiliations) and not any(
            cls._organization_overlap(expected, affiliation)
            for affiliation in affiliations
        )

    @classmethod
    def _location_overlap(cls, expected: str, actual: str) -> bool:
        # Nei valori "Avellino, Italy" il primo elemento identifica la città.
        # Confrontare l'intera stringa produrrebbe falsi match tra città diverse
        # che condividono soltanto il Paese.
        locality = expected.split(",", 1)[0].strip()
        return cls._overlap(locality, actual)
