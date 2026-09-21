import os
import logging
import logging_config
from multiprocessing import Pool
from urllib.parse import unquote

from scraper_manager import OlxScraper
from database_manager import DatabaseManager
from notification_manager import Messenger
from utils import BASE_DIR


scraper = OlxScraper()
db = DatabaseManager()


def load_target_urls() -> list:
    file_path = os.path.join(BASE_DIR, "target_urls.txt")

    user_message = (
        "The file 'target_urls.txt' has been created. "
        "Add in it at least one URL to monitor for new ads. "
        "Add 1 URL per line."
    )

    try:
        with open(file_path) as f:
            target_urls = [
                line.strip()
                for line in f
                if line.strip()
            ]
    except FileNotFoundError:
        logging.info(user_message)
        open(file_path, "w").close()
        target_urls = []

    if not target_urls:
        logging.info(user_message)

    return target_urls


def get_new_ads_urls(all_urls: list) -> list:
    return [
        url
        for url in all_urls
        if not db.url_exists(url)
    ]


def get_new_ads_urls_for_url(target_url: str) -> list:
    try:
        ads_urls = scraper.scrape_ads_urls(target_url)
    except ValueError as error:
        logging.error(error)
        return []

    return get_new_ads_urls(ads_urls)


def ad_matches_search(target_url: str, ad: dict) -> bool:
    """
    Reject obviously incorrect OLX search results.
    """

    target = unquote(target_url).lower()
    title = unquote(ad.get("title", "")).lower()

    # PS5 Pro
    if "ps5 pro" in target:
        return (
            "ps5 pro" in title
            or "playstation 5 pro" in title
        )

    # Xbox Series X
    if "xbox series x" in target:
        return (
            "series x" in title
            and "series s" not in title
        )

    # Xbox Series S
    if "xbox series s" in target:
        return (
            "series s" in title
            and "xbox 360" not in title
        )

    # Standard PS5 search
    if "/q-ps5/" in target:
        has_ps5 = (
            "ps5" in title
            or "playstation 5" in title
        )

        has_ps4 = (
            "ps4" in title
            or "playstation 4" in title
        )

        return has_ps5 and not has_ps4

    return True


def main() -> None:
    target_urls = load_target_urls()

    for target_url in target_urls:
        new_ads_urls = get_new_ads_urls_for_url(
            target_url
        )

        if not new_ads_urls:
            continue

        with Pool(10) as pool:
            ads = pool.map(
                scraper.get_ad_data,
                new_ads_urls
            )

        ads = list(filter(None, ads))

        matching_ads = [
            ad
            for ad in ads
            if ad_matches_search(target_url, ad)
        ]

        rejected_count = len(ads) - len(matching_ads)

        if rejected_count:
            logging.info(
                f"Rejected {rejected_count} "
                f"irrelevant OLX ads."
            )

        if matching_ads:
            message_subject, message_body = (
                Messenger.generate_email_content(
                    target_url,
                    matching_ads
                )
            )

            Messenger.send_email_message(
                message_subject,
                message_body
            )

            Messenger.send_telegram_message(
                message_subject,
                message_body
            )

        # Mark every processed URL as seen, including rejected ads.
        # Otherwise irrelevant listings would be downloaded every run.
        for url in new_ads_urls:
            db.add_url(url)


if __name__ == "__main__":
    main()