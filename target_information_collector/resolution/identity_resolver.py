from target_information_collector.shared.models import IdentityCandidate, PublicProfile, TargetInput

class IdentityResolver:
    def resolve(self, target: TargetInput, candidates: list, profiles: list[PublicProfile], evidence) -> dict:
        resolved_profiles = []
        resolved_candidates = []

        for profile in profiles:
            # 1. Escludiamo i profili palesemente di terzi (es. Carmine Calabrese)
            if not self._is_url_plausible(profile, target):
                continue

            evidence_for_profile = [ev for ev in evidence if ev.url == profile.url]
            text = self._profile_text(profile, evidence_for_profile)
            
            matched_fields = self._matched_fields(target, profile, text)
            
            # 2. Se è un omonimo accertato (es. Crotone/Bershka), lo scartiamo
            if self._is_explicit_omonym(target, matched_fields, text):
                continue

            # 3. COMPORTAMENTO TOLLERANTE PER SOCIAL (Facebook/Instagram):
            # Spesso gli snippet di FB/IG sono striminziti. Se l'URL è altamente compatibile 
            # (es. contiene nome.cognome o lo username esatto) e non ci sono prove contrarie,
            # lo teniamo come CANDIDATO anche se ha solo il Name Match, abbassando la confidence.
            if not matched_fields:
                continue
                
            confidence = self._score(profile, matched_fields, target)
            
            if not confidence >= 0.35:  # Soglia minima per considerare un candidato
                continue
            
            resolved_profiles.append(profile)
            resolved_candidates.append(
                IdentityCandidate(
                    candidate_id=f"{profile.platform}:{profile.username or profile.url}",
                    platform=profile.platform,
                    profile_url=profile.url,
                    confidence=confidence,
                    matched_fields=matched_fields,
                    evidence=evidence_for_profile,
                )
            )

        return {"identity_candidates": resolved_candidates, "public_profiles": resolved_profiles}

    def _is_url_plausible(self, profile: PublicProfile, target: TargetInput) -> bool:
        url_lower = profile.url.lower()
        name_parts = [part.lower() for part in target.full_name.split() if len(part) > 2]
        return any(part in url_lower for part in name_parts)

    def _is_explicit_omonym(self, target: TargetInput, matched_fields: list[str], text: str) -> bool:
        """Rileva se ci sono prove schiaccianti che si tratti di un'altra persona (es. Crotone, Torino, Bershka)."""
        # Se ha matchato la location corretta, non è un omonimo estraneo
        if "location" in matched_fields or "role" in matched_fields:
            return False
            
        # Se nel testo compaiono città totalmente diverse da quelle cercate (e l'utente ha specificato città target)
        if target.cities and "crotone" in text:
            return True
            
        return False

    def _matched_fields(self, target: TargetInput, profile: PublicProfile, text: str) -> list[str]:
        matched = []
        if target.full_name.lower() in text: 
            matched.append("name")
            
        all_cities = [c.lower() for c in target.cities if c]
        if target.location:
            all_cities.append(target.location.lower())
            
        if any(city in text for city in all_cities):
            matched.append("location")
            
        if target.role and target.role.lower() in text:
            matched.append("role")
            
        return matched

    def _score(self, profile: PublicProfile, matched_fields: list[str], target: TargetInput) -> float:
        """Calcola il punteggio in modo piramidale. 
        Solo nome = quasi zero. Nome + contesto = crescita progressiva.
        """
        # Se non c'è contesto e c'è SOLO il nome, il punteggio base è penalizzato al minimo
        if len(matched_fields) == 1 and "name" in matched_fields:
            base_score = 0.15
        else:
            # Se c'è contesto (es. Nome + Location), partiamo da 0.35 
            # e aggiungiamo un peso forte (+0.25) per ogni campo extra verificato
            base_score = 0.35 + (0.25 * (len(matched_fields) - 1))
        
        # Bonus se l'URL è pulito ed è un social network importante
        url_lower = profile.url.lower()
        if profile.platform in {"facebook", "instagram", "linkedin"}:
            cleaned_name = target.full_name.lower().replace(" ", "")
            if cleaned_name in url_lower.replace(".", "").replace("-", ""):
                base_score += 0.1
                
        return round(min(0.99, max(0.05, base_score)), 2)

    def _profile_text(self, profile: PublicProfile, evidence_for_profile) -> str:
        fragments = [profile.url]
        for ev in evidence_for_profile:
            if ev.title: fragments.append(ev.title)
            if ev.description: fragments.append(ev.description)
            if ev.value: fragments.append(ev.value)
        return " ".join(fragments).lower()