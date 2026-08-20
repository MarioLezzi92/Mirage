import phishing_campaign_generator


# True usa i profili salvati; False esegue la raccolta live.
USE_MOCK = True


def main() -> None:
    if not USE_MOCK:
        from target_information_collector.core.target_information_service import (
            run_live,
        )

        run_live()

    campaigns = phishing_campaign_generator.generate()
    print(f"Campagne generate: {len(campaigns)}")


if __name__ == "__main__":
    main()