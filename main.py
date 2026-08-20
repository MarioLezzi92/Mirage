import campaign_launcher
import nlp_text_personalizer
import phishing_campaign_generator


# True usa gli output salvati dei moduli 1-3; False li rigenera.
USE_MOCK = True
# True prepara gli invii senza spedire email; False usa SMTP reale.
DRY_RUN = True


def main() -> None:
    print("=== SocialEng pipeline ===\n")

    print("[1/4] TARGET INFORMATION COLLECTOR")
    if USE_MOCK:
        print("      MOCK: uso i profili salvati, nessuna chiamata Apify")
    else:
        print("      LIVE: raccolta e normalizzazione dei target")
        from target_information_collector import run_live

        run_live()
    print("      Output: profili strutturati")

    print("\n[2/4] PHISHING CAMPAIGN GENERATOR")
    if USE_MOCK:
        print("      MOCK: uso le campagne salvate")
    else:
        campaigns = phishing_campaign_generator.generate()
        print(f"      Output: {len(campaigns)} campagne email")

    print("\n[3/4] NLP TEXT PERSONALIZER")
    if USE_MOCK:
        print("      MOCK: uso i messaggi personalizzati salvati")
    else:
        messages = nlp_text_personalizer.run()
        print(f"      Output: {len(messages)} messaggi personalizzati")

    print("\n[4/4] CAMPAIGN LAUNCHER")
    print(f"      Modalità: {'DRY-RUN' if DRY_RUN else 'SMTP LIVE'}")
    deliveries = campaign_launcher.run(dry_run=DRY_RUN)

    prepared = sum(item.status == "prepared" for item in deliveries)
    sent = sum(item.status == "sent" for item in deliveries)
    failed = sum(item.status == "failed" for item in deliveries)

    print(f"      Output: {len(deliveries)} delivery")
    print("\n=== Pipeline completata ===")
    print(f"Preparati: {prepared} | Inviati: {sent} | Falliti: {failed}")


if __name__ == "__main__":
    main()