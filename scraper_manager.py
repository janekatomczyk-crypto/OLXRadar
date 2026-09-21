import re
import logging
import logging_config

from curl_cffi import requests
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from bs4 import BeautifulSoup, ResultSet, Tag
from utils import get_header


class OlxScraper:
    """Scraper used to monitor listings on OLX.pl."""

    def __init__(self):
        self.headers = get_header()
        self.netloc = "www.olx.pl"
        self.schema = "https"
        self.current_page = 1
        self.last_page = None

    def parse_content(self, target_url: str) -> BeautifulSoup:
        """
        Download and parse an OLX page.
        """
        try:
            r = requests.get(
                target_url,
                headers=self.headers,
                impersonate="chrome",
                timeout=60
            )
            r.raise_for_status()

        except Exception as error:
            logging.error(f"Connection error: {error}")
            return None

        return BeautifulSoup(r.text, "html.parser")

    def get_ads(self, parsed_content: BeautifulSoup) -> ResultSet[Tag]:
        """
        Find listing cards on an OLX search results page.
        """
        if parsed_content is None:
            return []

        return parsed_content.select(
            'div[data-testid="l-card"], div[data-cy="l-card"]'
        )

    def get_last_page(self, parsed_content: BeautifulSoup) -> int:
        """
        Try to determine the last available results page.
        """
        if parsed_content is None:
            return None

        pagination_ul = parsed_content.find(
            "ul",
            class_="pagination-list"
        )

        if pagination_ul is not None:
            pages = pagination_ul.find_all(
                "li",
                class_="pagination-item"
            )

            page_numbers = []

            for page in pages:
                text = page.get_text(strip=True)

                if text.isdigit():
                    page_numbers.append(int(text))

            if page_numbers:
                return max(page_numbers)

        return None

    def scrape_ads_urls(self, target_url: str) -> list:
        """
        Find advertisement URLs in an OLX.pl search.

        Existing search parameters such as price and sorting are preserved.
        """
        ads_links = set()

        self.current_page = 1
        self.last_page = None

        parsed_target = urlparse(target_url)

        if parsed_target.netloc != self.netloc:
            raise ValueError(
                f"Bad URL! OLXRadar is configured to process "
                f"{self.netloc} links only."
            )

        while True:
            parsed_url = urlparse(target_url)

            query = dict(
                parse_qsl(
                    parsed_url.query,
                    keep_blank_values=True
                )
            )

            query["page"] = str(self.current_page)

            url = urlunparse(
                parsed_url._replace(
                    query=urlencode(query)
                )
            )

            logging.info(
                f"Processing search page: {url}"
            )

            parsed_content = self.parse_content(url)

            if parsed_content is None:
                break

            self.last_page = self.get_last_page(
                parsed_content
            )

            ads = self.get_ads(parsed_content)

            if not ads:
                break

            for ad in ads:
                link = ad.find(
                    "a",
                    href=lambda href:
                        href is not None
                        and "/d/oferta/" in href
                )

                if link is None:
                    continue

                link_href = link.get("href")

                if not link_href:
                    continue

                if not self.is_internal_url(
                    link_href,
                    self.netloc
                ):
                    continue

                if not self.is_relevant_url(link_href):
                    continue

                if self.is_relative_url(link_href):
                    link_href = (
                        f"{self.schema}://"
                        f"{self.netloc}"
                        f"{link_href}"
                    )

                ads_links.add(link_href)

            if (
                self.last_page is None
                or self.current_page >= self.last_page
            ):
                break

            self.current_page += 1

        return list(ads_links)

    def is_relevant_url(self, url: str) -> bool:
        """
        Check whether a URL points to an OLX advertisement.
        """
        parsed_url = urlparse(url)

        return "/d/oferta/" in parsed_url.path

    def is_internal_url(
        self,
        url: str,
        domain: str
    ) -> bool:
        """
        Check whether a URL belongs to OLX.pl
        or is a relative URL.
        """
        if self.is_relative_url(url):
            return True

        parsed_url = urlparse(url)

        return parsed_url.netloc == domain

    def is_relative_url(self, url: str) -> bool:
        """
        Check whether a URL is relative.
        """
        parsed_url = urlparse(url)

        if not parsed_url.netloc:
            return True

        if re.search(r"^/[\w.\-/]+", url):
            return True

        return False

    def get_ad_data(self, ad_url: str) -> dict[str]:
        """
        Extract title, price and description
        from an OLX.pl advertisement.
        """
        logging.info(
            f"Processing advertisement: {ad_url}"
        )

        content = self.parse_content(ad_url)

        if content is None:
            return None

        title = None
        price = None
        description = None

        # -------------------------
        # TITLE
        # -------------------------

        og_title = content.find(
            "meta",
            property="og:title"
        )

        if og_title is not None:
            title = og_title.get("content")

            if title:
                title = re.sub(
                    r"\s*•\s*OLX\.pl\s*$",
                    "",
                    title
                ).strip()

        # Fallback if OLX changes og:title
        if title is None:
            title_element = content.find("h1")

            if title_element is not None:
                title = title_element.get_text(
                    " ",
                    strip=True
                )

        # -------------------------
        # PRICE
        # -------------------------

        price_element = content.find(
            "h3",
            class_="css-yauxmy"
        )

        if price_element is not None:
            price = price_element.get_text(
                " ",
                strip=True
            )

        # Fallback
        if price is None:
            price_container = content.find(
                attrs={
                    "data-testid": "ad-price-container"
                }
            )

            if price_container is not None:
                price = price_container.get_text(
                    " ",
                    strip=True
                )

        # -------------------------
        # DESCRIPTION
        # -------------------------

        description_element = content.find(
            attrs={
                "data-cy": "ad_description"
            }
        )

        if description_element is not None:
            description = description_element.get_text(
                "\n",
                strip=True
            )

        # -------------------------
        # VALIDATION
        # -------------------------

        if any(
            item is None
            for item in [
                title,
                price,
                description
            ]
        ):
            logging.warning(
                f"Missing ad data for {ad_url}: "
                f"title={title is not None}, "
                f"price={price is not None}, "
                f"description={description is not None}"
            )

            return None

        return {
            "title": title,
            "price": price,
            "url": ad_url,
            "description": description
        }