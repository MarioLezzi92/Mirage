import campaign_launcher
import interaction_logger
import nlp_text_personalizer
import phishing_campaign_generator
import risk_report_generator
import target_information_collector


# True usa i dati salvati; False esegue la raccolta live.
USE_MOCK = True
# True rigenera campagne e messaggi.
REBUILD_CONTENT = True
# True invia davvero via SMTP; False prepara soltanto il delivery.
SEND_EMAILS = True


def main() -> None:
    print("=== SocialEng pipeline ===\n")
    rebuild = not USE_MOCK or REBUILD_CONTENT

    print("[1/6] TARGET INFORMATION COLLECTOR")
    if USE_MOCK:
        print("      MOCK: uso i profili salvati, nessuna chiamata Apify")
        profiles = target_information_collector.load_profiles()
    else:
        print("      LIVE: raccolta e normalizzazione dei target")
        profiles = [result.profile for result in target_information_collector.run()]
    if len(profiles) != 1:
        raise ValueError(
            "La pipeline richiede esattamente un target in target_input.json"
        )
    profile = profiles[0]
    print(f"      Target corrente: {profile.name}")
    print("      Output: 1 profilo strutturato\n")

    if not profile.emails:
        print("=== Pipeline arrestata senza errori ===")
        print(f"Nessuna email pubblica verificata per {profile.name}.")
        return

    print("[2/6] PHISHING CAMPAIGN GENERATOR")
    if USE_MOCK and not REBUILD_CONTENT:
        print("      MOCK: uso la campagna salvata")
    else:
        campaigns = phishing_campaign_generator.generate([profile])
        print("      Output: 1 campagna email")

    print("\n[3/6] NLP TEXT PERSONALIZER")
    if rebuild:
        messages = nlp_text_personalizer.run(
            campaigns=campaigns,
            profiles=[profile],
        )
    else:
        print("      MOCK: uso il messaggio personalizzato salvato")
        messages = nlp_text_personalizer.run(
            use_mock=True,
            targets=[profile.name],
        )
    print("      Output: 1 messaggio personalizzato")

    print("\n[4/6] CAMPAIGN LAUNCHER")
    print(f"      Modalità: {'SMTP LIVE' if SEND_EMAILS else 'DRY-RUN'}")
    logger = None
    try:
        if SEND_EMAILS:
            print("      Avvio automatico del tunnel pubblico")
        logger = interaction_logger.start(public=SEND_EMAILS, deliveries=[])
        deliveries = campaign_launcher.run(
            dry_run=not SEND_EMAILS,
            base_url=logger.tracking_base_url,
            messages=messages,
        )
        failed = [item for item in deliveries if item.status == "failed"]
        if failed:
            raise RuntimeError(f"Delivery fallito: {failed[0].error}")
        logger.authorize(deliveries)
        print("      Output: 1 delivery")

        print("\n[5/6] INTERACTION LOGGER")
        print("      Registra click, invii del form e download")
        print("      Premi Ctrl+C quando vuoi generare il report")
        logger.wait()
    finally:
        if logger:
            logger.close()

    print("\n[6/6] RISK REPORT GENERATOR")
    reports = risk_report_generator.run(deliveries=deliveries)
    print(f"      JSON: {reports['json']}")
    print(f"      HTML: {reports['html']}")
    print("\n=== Pipeline terminata ===")


if __name__ == "__main__":
    main()
