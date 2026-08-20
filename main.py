import nlp_text_personalizer
import phishing_campaign_generator


# True usa i profili salvati; False esegue anche la raccolta live.
USE_MOCK = True


def main() -> None:
    print("=== SocialEng pipeline ===")

    print("\n[1/3] TARGET INFORMATION COLLECTOR")
    print("      Raccoglie e normalizza le informazioni dei target")
    if USE_MOCK:
        print("      Modalità: MOCK — uso i profili salvati, nessuna chiamata Apify")
    else:
        print("      Modalità: LIVE — raccolta tramite API e agenti")
        from target_information_collector.core.target_information_service import run_live

        run_live()
    print("      Output: profili strutturati disponibili in data/profiles")

    print("\n[2/3] PHISHING CAMPAIGN GENERATOR")
    print("      Input: profili strutturati presenti in data/profiles")
    print("      Seleziona dinamicamente il template più adatto")
    campaigns = phishing_campaign_generator.generate()
    print(f"      Output: {len(campaigns)} campagne email")

    print("\n[3/3] NLP TEXT PERSONALIZER")
    print(f"      Input: {len(campaigns)} campagne email")
    print("      Personalizza i messaggi con Ollama / Gemma2")
    messages = nlp_text_personalizer.run()
    print(f"      Output: {len(messages)} messaggi personalizzati")

    print("\n=== Pipeline completata ===")
    print(f"Campagne: {len(campaigns)} | Messaggi: {len(messages)}")


if __name__ == "__main__":
    main()