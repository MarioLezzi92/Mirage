import campaign_launcher
import interaction_logger
import nlp_text_personalizer
import phishing_campaign_generator


# True evita Apify e usa profili/campagne salvati.
USE_MOCK = True
# True rigenera i messaggi con Ollama/Gemma2.
REBUILD_CONTENT = True
# False prepara soltanto i delivery; True invia via SMTP.
SEND_EMAILS = True


def main() -> None:
    print("=== SocialEng pipeline ===\n")
    rebuild = not USE_MOCK or REBUILD_CONTENT

    print("[1/5] TARGET INFORMATION COLLECTOR")
    if USE_MOCK:
        print("      MOCK: uso i profili salvati, nessuna chiamata Apify")
    else:
        print("      LIVE: raccolta e normalizzazione dei target")
        from target_information_collector.core.target_information_service import run_live

        run_live()
    print("      Output: profili strutturati\n")

    print("[2/5] PHISHING CAMPAIGN GENERATOR")
    if USE_MOCK and not REBUILD_CONTENT:
        print("      MOCK: uso le campagne salvate")
    else:
        campaigns = phishing_campaign_generator.generate()
        print(f"      Output: {len(campaigns)} campagne email")

    print("\n[3/5] NLP TEXT PERSONALIZER")
    if rebuild:
        messages = nlp_text_personalizer.run()
        print(f"      Output: {len(messages)} messaggi personalizzati")
    else:
        print("      MOCK: uso i messaggi personalizzati salvati")

    print("\n[4/5] CAMPAIGN LAUNCHER")
    if not rebuild and not SEND_EMAILS:
        print("      MOCK: uso i delivery salvati, nessun invio SMTP")
    else:
        mode = "SMTP LIVE" if SEND_EMAILS else "DRY-RUN"
        print(f"      Modalità: {mode}")
        deliveries = campaign_launcher.run(dry_run=not SEND_EMAILS)
        print(f"      Output: {len(deliveries)} delivery")

    print("\n[5/5] INTERACTION LOGGER")
    print("      Registra i click tramite tracking UUID")
    interaction_logger.run()

    print("\n=== Pipeline terminata ===")


if __name__ == "__main__":
    main()
