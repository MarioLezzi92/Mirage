import re

from target_information_collector.shared.models import (
    Evidence,
    EvidenceType,
    ProfileData,
    TargetInput,
)
from target_information_collector.shared.text import (
    canonical_url,
    email_owner_matches,
    normalize,
    profile_owner_matches,
    profile_username,
    tokens,
)


class IdentityMatcher:
    """Verifica gli omonimi con regole deterministiche e spiegabili."""

    ORGANIZATION_MARKERS = {
        "accademia", "academy", "azienda", "college", "company",
        "department", "dipartimento", "firm", "institute", "istituto",
        "school", "scuola", "societa", "studio", "universita", "university",
    }
    ORGANIZATION_WEAK_TOKENS = {"san", "saint", "st"}
    CONTEXT_WEAK_TOKENS = {
        "componenti", "official", "officiale", "page", "pagina",
        "profile", "profilo",
    }
    IDENTITY_GENERIC_TOKENS = {
        "account", "analyst", "application", "campania", "company", "custom",
        "developer", "engineer", "engineering", "italia", "italy",
        "manager", "management", "official", "profile", "software",
        "attualmente", "corso", "formazione", "laurea", "lavoro",
        "magistrale", "ricerca", "student", "studente", "studentessa",
        "triennale",
    }

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
        if name_matches and username_matches:
            score += 0.1
            reasons.append("nome e username concordanti")
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
        if target.corroboration and self._corroborates(
            target.full_name,
            context,
            target.corroboration,
        ):
            score += 0.2
            reasons.append("evidenze indipendenti compatibili")

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

    @classmethod
    def resolve_profiles(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
    ) -> dict[str, Evidence]:
        """Seleziona un solo profilo coerente per piattaforma o si astiene."""
        profiles = cls._profile_candidates(target.full_name, evidence)
        contexts = {
            key: cls._identity_tokens(target, evidence, key)
            for key in profiles
        }
        web_context = cls._verified_web_tokens(target.full_name, evidence)
        direct: dict[str, int] = {}
        explicit: dict[str, bool] = {}
        contextual: dict[str, int] = {}
        web_supported: dict[str, bool] = {}
        partners: dict[str, set[str]] = {key: set() for key in profiles}

        for key, item in profiles.items():
            reasons = set(item.metadata.get("reasons") or [])
            explicit[key] = "profilo fornito esplicitamente" in reasons
            contextual[key] = len(reasons & {
                "evidenze indipendenti compatibili",
                "formazione compatibile",
                "località compatibile",
                "ruolo compatibile",
            })
            direct[key] = cls._direct_anchor_count(target, evidence, key)
            web_supported[key] = bool(
                web_context and cls._contexts_match(contexts[key], web_context)
            )

        items = list(profiles.items())
        for index, (left_key, left) in enumerate(items):
            for right_key, right in items[index + 1:]:
                if left.platform == right.platform:
                    continue
                linked = (
                    right_key in cls._related_profile_keys(left)
                    or left_key in cls._related_profile_keys(right)
                )
                same_handle = cls._same_handle_family(
                    target.full_name,
                    left,
                    right,
                )
                if linked or same_handle or cls._contexts_match(
                    contexts[left_key],
                    contexts[right_key],
                    allow_single=bool(web_context),
                ):
                    partners[left_key].add(right.platform)
                    partners[right_key].add(left.platform)

        supported = {
            key for key in profiles
            if explicit[key]
            or direct[key] >= 2
            or contextual[key] >= 2
            or web_supported[key]
            or profiles[key].confidence >= 0.8 and direct[key] >= 1
            or profiles[key].confidence >= 0.9 and contextual[key] >= 1
            or bool(web_context) and direct[key] >= 1 and partners[key]
        }
        changed = True
        while changed:
            changed = False
            for key, item in profiles.items():
                if key in supported:
                    continue
                for trusted_key in tuple(supported):
                    trusted = profiles[trusted_key]
                    if (
                        item.platform != trusted.platform
                        and cls._contexts_match(
                            contexts[key],
                            contexts[trusted_key],
                            allow_single=False,
                        )
                    ):
                        supported.add(key)
                        partners[key].add(trusted.platform)
                        partners[trusted_key].add(item.platform)
                        changed = True
                        break

        resolved: dict[str, Evidence] = {}
        for platform in sorted({profiles[key].platform for key in supported}):
            keys = [
                key for key in supported
                if profiles[key].platform == platform
            ]
            keys.sort(
                key=lambda key: (
                    explicit[key],
                    min(direct[key], 2),
                    min(contextual[key], 2),
                    web_supported[key],
                    len(partners[key]),
                    profiles[key].confidence,
                ),
                reverse=True,
            )
            if len(keys) == 1:
                resolved[keys[0]] = profiles[keys[0]]
                continue
            best, second = keys[:2]
            best_support = (
                explicit[best], min(direct[best], 2),
                min(contextual[best], 2),
                web_supported[best], len(partners[best]),
            )
            second_support = (
                explicit[second], min(direct[second], 2),
                min(contextual[second], 2),
                web_supported[second], len(partners[second]),
            )
            if (
                best_support != second_support
                or profiles[best].confidence - profiles[second].confidence > 0.05
            ):
                resolved[best] = profiles[best]
        return resolved

    @classmethod
    def _related_profile_keys(cls, item: Evidence) -> set[str]:
        # La presenza di due URL nei risultati della stessa query è soltanto
        # un'ipotesi di discovery, non una prova che appartengano alla stessa
        # persona. I crosslink letti direttamente da un profilo restano validi.
        if item.metadata.get("search_crosslink"):
            return set()
        values = item.metadata.get("related_profiles") or []
        return {
            cls._url_key(value)
            for value in values
            if isinstance(value, str) and value
        }

    @classmethod
    def _same_handle_family(
        cls,
        full_name: str,
        left: Evidence,
        right: Evidence,
    ) -> bool:
        """Riconosce solo handle distintivi identici tra piattaforme."""
        parts = normalize(full_name).split()
        if len(parts) < 2:
            return False
        expected = {f"{parts[0]}{parts[-1]}", f"{parts[-1]}{parts[0]}"}

        def handle(item: Evidence) -> str:
            username = profile_username(str(item.url), item.platform) if item.url else ""
            return normalize(username).replace(" ", "")

        left_handle, right_handle = handle(left), handle(right)
        return bool(
            left_handle
            and left_handle == right_handle
            and left_handle not in expected
            and max(left.confidence, right.confidence) >= 0.8
        )

    @classmethod
    def _profile_candidates(
        cls,
        full_name: str,
        evidence: list[Evidence],
    ) -> dict[str, Evidence]:
        profiles: dict[str, Evidence] = {}
        for item in evidence:
            if item.evidence_type != EvidenceType.PROFILE or not item.url:
                continue
            reasons = item.metadata.get("reasons") or []
            if (
                item.platform == "facebook"
                and "verifica tramite discovery pubblica" in reasons
                and not profile_owner_matches(
                    full_name,
                    username=profile_username(str(item.url), item.platform),
                )
            ):
                continue
            key = cls._url_key(item.url)
            current = profiles.get(key)
            if current is None or item.confidence > current.confidence:
                profiles[key] = item
        return profiles

    @classmethod
    def _direct_anchor_count(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
        url_key: str,
    ) -> int:
        facts = [
            item
            for item in evidence
            if item.url
            and cls._url_key(item.url) == url_key
            and item.evidence_type != EvidenceType.PROFILE
        ]
        context = " ".join(item.value for item in facts)
        anchors = sum((
            bool(target.company and cls._organization_overlap(target.company, context)),
            bool(target.role and cls._overlap(target.role, context)),
            bool(target.department and cls._overlap(target.department, context)),
            bool(target.cities and any(
                cls._location_overlap(city, context) for city in target.cities
            )),
            bool(target.education and any(
                cls._overlap(value, context) for value in target.education
            )),
        ))
        if target.email and any(
            item.evidence_type == EvidenceType.EMAIL
            and item.value.casefold() == target.email.casefold()
            for item in facts
        ):
            anchors += 2
        return anchors

    @classmethod
    def _identity_tokens(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
        url_key: str,
    ) -> set[str]:
        excluded = tokens(" ".join(
            value for value in (
                target.full_name, target.company, target.role,
                *target.cities, *target.education,
            ) if value
        )) | cls.IDENTITY_GENERIC_TOKENS
        if target.company:
            excluded |= cls.organization_aliases(target.company)
        values = " ".join(
            item.value
            for item in evidence
            if item.url
            and cls._url_key(item.url) == url_key
            and item.evidence_type in {
                EvidenceType.EMAIL, EvidenceType.ROLE, EvidenceType.BIO,
                EvidenceType.COMPANY, EvidenceType.LOCATION,
                EvidenceType.EDUCATION,
            }
        )
        return {value for value in tokens(values) if len(value) >= 5} - excluded

    @classmethod
    def _verified_web_tokens(
        cls,
        full_name: str,
        evidence: list[Evidence],
    ) -> set[str]:
        if not any(
            item.platform == "web"
            and item.evidence_type == EvidenceType.EMAIL
            and email_owner_matches(full_name, item.value)
            for item in evidence
        ):
            return set()
        values = " ".join(
            item.value
            for item in evidence
            if item.platform == "web"
            and item.evidence_type == EvidenceType.WEB_MENTION
        )
        return {
            value for value in tokens(values) if len(value) >= 5
        } - tokens(full_name) - cls.IDENTITY_GENERIC_TOKENS

    @staticmethod
    def _contexts_match(
        left: set[str],
        right: set[str],
        *,
        allow_single: bool = False,
    ) -> bool:
        matches: set[tuple[str, str]] = set()
        used: set[str] = set()
        for left_token in left:
            for right_token in right:
                related = (
                    left_token == right_token
                    or min(len(left_token), len(right_token)) >= 5
                    and (
                        left_token.startswith(right_token)
                        or right_token.startswith(left_token)
                    )
                )
                if related and right_token not in used:
                    matches.add((left_token, right_token))
                    used.add(right_token)
                    break
        return len(matches) >= 2 or allow_single and any(
            min(len(left), len(right)) >= 7 for left, right in matches
        )

    @staticmethod
    def _url_key(url) -> str:
        return canonical_url(str(url)).casefold()

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
        expected_normalized = normalize(expected)
        actual_normalized = normalize(actual)
        if not expected_normalized or not actual_normalized:
            return False

        if re.search(
            rf"(?:^| ){re.escape(expected_normalized)}(?: |$)",
            actual_normalized,
        ):
            return True

        actual_words = set(actual_normalized.split())
        aliases = cls.organization_aliases(expected)
        if aliases & actual_words:
            return True

        # Gli account universitari incorporano spesso l'alias dell'ateneo
        # nell'handle (es. "dinfunisa"). È comunque un riferimento forte,
        # purché l'alias universitario sia completo e in coda al token.
        if any(
            len(alias) >= 5
            and any(
                word.endswith(alias)
                and len(word) <= len(alias) + 12
                for word in actual_words
            )
            for alias in aliases
        ):
            return True

        expected_tokens = tokens(expected) - cls.ORGANIZATION_WEAK_TOKENS
        actual_tokens = tokens(actual) - cls.ORGANIZATION_WEAK_TOKENS
        shared = expected_tokens & actual_tokens
        if len(shared) >= 2:
            return True

        expected_words = set(expected_normalized.split())
        return bool(
            len(shared) == 1
            and expected_words & cls.ORGANIZATION_MARKERS
            and actual_words & cls.ORGANIZATION_MARKERS
        )

    @classmethod
    def organization_aliases(cls, value: str) -> set[str]:
        """Ricava alias universitari brevi senza dizionari legati al target."""
        normalized = normalize(value)
        if not normalized.startswith(("universita ", "university ")):
            return set()
        meaningful = [
            part
            for part in normalized.split()
            if part in tokens(value)
            and part not in cls.ORGANIZATION_WEAK_TOKENS
        ]
        return {f"uni{meaningful[-1][:2]}"} if meaningful else set()

    @classmethod
    def _corroborates(
        cls,
        full_name: str,
        candidate_context: str,
        independent_contexts: list[str],
    ) -> bool:
        name_tokens = tokens(full_name)
        candidate_tokens = cls._context_tokens(candidate_context, name_tokens)
        if len(candidate_tokens) < 2:
            return False

        for independent in independent_contexts:
            source_tokens = cls._context_tokens(independent, name_tokens)
            matched_candidate: set[str] = set()
            matched_source: set[str] = set()
            for left in candidate_tokens:
                for right in source_tokens:
                    if right in matched_source or not cls._related(left, right):
                        continue
                    matched_candidate.add(left)
                    matched_source.add(right)
                    break
            if len(matched_candidate) >= 2:
                return True
        return False

    @classmethod
    def _context_tokens(cls, value: str, excluded: set[str]) -> set[str]:
        return {
            part
            for part in tokens(value)
            if len(part) >= 5
            and part not in excluded
            and part not in cls.CONTEXT_WEAK_TOKENS
        }

    @staticmethod
    def _related(left: str, right: str) -> bool:
        if left == right:
            return True
        common = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            common += 1
        return common >= 5

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
