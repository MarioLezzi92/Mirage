from target_information_collector.collectors.facebook_agent import FacebookAgent
from target_information_collector.collectors.github_agent import GitHubAgent
from target_information_collector.collectors.instagram_agent import InstagramAgent
from target_information_collector.collectors.linkedin_agent import LinkedInAgent
from target_information_collector.collectors.web_agent import WebAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import TargetInput

class ProfileBuilder:
    """
    Orchestratore della FASE 1: Raccolta Dati Grezzi (Raw Gathering).
    Esegue un ciclo iterativo per permettere agli agenti di fare ricerche incrociate 
    basandosi sulle evidenze via via accumulate nello store.
    """
    def __init__(self):
        self.github_agent = GitHubAgent()
        self.linkedin_agent = LinkedInAgent()
        self.facebook_agent = FacebookAgent()
        self.instagram_agent = InstagramAgent()
        self.web_agent = WebAgent()

    def collect_raw(self, target: TargetInput) -> dict:
        store = EvidenceStore(target)

        # 1. Baseline Gathering: esplorazione iniziale con i dati di input deterministici
        self.web_agent.collect_base(store)
        self.github_agent.collect(store)
        self.linkedin_agent.collect(store)

        # 2. Cross-Referenced Loops: ciclo guidato dall'espansione delle evidenze.
        previous_evidence_count = 0
        iteration = 0
        max_iterations = 2 

        while len(store.evidence) > previous_evidence_count and iteration < max_iterations:
            previous_evidence_count = len(store.evidence)
            
            # Gli agenti attingono allo store aggiornato per stringere il cerchio sui social
            self.web_agent.collect_social_contextual(store)
            self.facebook_agent.collect(store)
            self.instagram_agent.collect(store)
            
            iteration += 1

        return {
            "candidates": [c.model_dump(mode="json") for c in store.candidates],
            "evidence": [e.model_dump(mode="json") for e in store.evidence],
        }