import campaign_launcher
import interaction_logger
import nlp_text_personalizer
import phishing_campaign_generator


# True usa gli output salvati dei moduli 1-4; False li rigenera.
USE_MOCK = True
# Usato solo con USE_MOCK=False: True non spedisce email.
DRY_RUN = True


def main() -> None:
    print("=== SocialEng pipeline ===\n")

    print("[1/5] TARGET INFORMATION COLLECTOR")
    if USE_MOCK:
        print("      MOCK: uso i profili salvati, nessuna chiamata Apify")
    else:
        print("      LIVE: raccolta e normalizzazione dei target")
        from target_information_collector.core.target_information_service import run_live

        run_live()
    print("      Output: profili strutturati\n")

    print("[2/5] PHISHING CAMPAIGN GENERATOR")
    if USE_MOCK:
        print("      MOCK: uso le campagne salvate")
    else:
        campaigns = phishing_campaign_generator.generate()
        print(f"      Output: {len(campaigns)} campagne email")

    print("\n[3/5] NLP TEXT PERSONALIZER")
    if USE_MOCK:
        print("      MOCK: uso i messaggi personalizzati salvati")
    else:
        messages = nlp_text_personalizer.run()
        print(f"      Output: {len(messages)} messaggi personalizzati")

    print("\n[4/5] CAMPAIGN LAUNCHER")
    if USE_MOCK:
        print("      MOCK: uso i delivery salvati, nessun invio SMTP")
    else:
        print(f"      Modalità: {'DRY-RUN' if DRY_RUN else 'SMTP LIVE'}")
        deliveries = campaign_launcher.run(dry_run=DRY_RUN)
        print(f"      Output: {len(deliveries)} delivery")

    print("\n[5/5] INTERACTION LOGGER")
    print("      Registra i click tramite tracking UUID")
    interaction_logger.run()

    print("\n=== Pipeline terminata ===")


if __name__ == "__main__":
    main()