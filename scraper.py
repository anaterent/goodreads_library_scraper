import json
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import re

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
}

RSS_PER_PAGE = 30
LIBRARY_WORKERS = 5
SPYDUS_SEARCH_URL = "https://wml.spydus.com/cgi-bin/spydus.exe/ENQ/OPAC/BIBWRKENQ"
DETAIL_REQUEST_HEADERS = {
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


class GoodreadsScraper:
    def __init__(self, user, list_name) -> None:
        # Store the Goodreads account and shelf name so later RSS requests use the
        # correct feed URL and filter options.
        self.user = user
        self.list_name = list_name
        self.books = []
        self.rss_base_url = f"https://www.goodreads.com/review/list_rss/{self.user}"

    def fetch_rss_page(self, page):
        # Goodreads exposes shelf content through an RSS feed. We request one page at a
        # a time and parse the XML items into Python objects.
        params = {
            "shelf": self.list_name,
            "per_page": RSS_PER_PAGE,
            "page": page,
        }

        try:
            response = requests.get(
                self.rss_base_url, params=params, headers=HEADERS, timeout=30
            )
            if response.status_code != 200:
                print(f"Page {page} fetch failed, Status: {response.status_code}")
                return []
            root = ET.fromstring(response.content)
            return root.findall("./channel/item")
        except (requests.RequestException, ET.ParseError) as e:
            print(f"Request error on page {page}: {e}")
            return []

    def parse_items(self, items, page) -> bool:
        # A page can be empty, which usually means the feed has ended. Returning False
        # stops the pagination loop cleanly.
        if not items:
            print(f"Page {page}: No books found.")
            return False

        for item in items:
            # Goodreads RSS items contain the title, author, rating, and sometimes an
            # ISBN. We only keep the data that is useful for later Spydus lookups.
            title = item.findtext("title", default="").strip()
            author = item.findtext("author_name", default="").strip()
            rating = item.findtext("average_rating", default="").strip()
            isbn = item.findtext("isbn", default="").strip()
            if not title:
                continue

            book = {"title": title, "author": author, "rating": rating}
            if isbn:
                book["isbn"] = isbn
            self.books.append(book)

        print(f"Page {page}: Found {len(items)} books.")
        return True

    def scrape_goodreads_list(
        self, page: int = 1, page_limit: float | int = float("inf")
    ) -> list:
        """Fetch books from a Goodreads shelf via RSS."""
        current_page = page
        pages_fetched = 0

        while True:
            items = self.fetch_rss_page(current_page)
            if not self.parse_items(items, current_page):
                break

            pages_fetched += 1
            if page_limit != float("inf") and pages_fetched >= int(page_limit):
                break
            if len(items) < RSS_PER_PAGE:
                break
            current_page += 1

        return self.books


class LibraryScraper:
    def __init__(self, workers: int = LIBRARY_WORKERS) -> None:
        # The scraper can resolve several books at once, but we keep the worker count
        # modest so the library site is not overwhelmed by concurrent requests.
        self.workers = workers

    def books_at_branch(self, books: list[dict], branch: str) -> list[dict]:
        """Look up each book in parallel and return only the copies available at the branch."""
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # The lambda keeps the code compact while still letting each book run through
            # the full enrichment flow independently.
            enriched = executor.map(lambda book: self.enrich_book(book, branch), books)
        return [book for book in enriched if book is not None]

    def enrich_book(self, book: dict, branch: str) -> dict | None:
        # Resolve the book in the library catalogue and, if a match is found, fetch the
        # holdings page for that specific record.
        match = self.search_catalog(book)

        if match is None:
            return None
        print("library match:", match)

        # Once the catalogue URL has been resolved, fetch the holdings page and then
        # filter the results down to the specific branch the user asked for.
        holdings = self.get_holdings(match["availability_url"])
        branch_holdings = [
            holding for holding in holdings if holding["location"] == branch
        ]
        if not branch_holdings:
            return None

        enriched = dict(book)
        enriched["availability"] = branch_holdings
        # if match["cover_url"]:
        #     enriched["img_url"] = match["cover_url"]
        return enriched

    def search_catalog(self, book: dict) -> dict | None:
        # The full catalogue search is the entry point for each book. We build a query
        # from the Goodreads title/author fields and, when available, the ISBN.
        query = self._build_search_query(book)
        params = {
            "ENTRY": query,
            "ENTRY_NAME": "BS",
            "ENTRY_TYPE": "K",
            "SORTS": "SQL_REL_WRK",
            "GQ": query,
            "CF": "GEN",
            "NRECS": "20",
            "QRY": "",
            "QRYTEXT": "Full catalogue",
            "_SPQ": "2",
        }

        try:
            response = requests.get(
                SPYDUS_SEARCH_URL,
                params=params,
                headers=HEADERS,
                timeout=30,
            )
        
        except requests.RequestException as e:
            print(f"Search failed for {book['title']!r}: {e}")
            return None

        if response.status_code != 200:
            print(
                f"Search failed for {book['title']!r}, Status: {response.status_code}"
            )
            return None

        soup = BeautifulSoup(response.content, "html.parser")
        # Each result card is evaluated independently. A card can be skipped if it is
        # clearly not the requested physical book, or it can be accepted once its title
        # and author are close enough to the Goodreads metadata.
        for result in soup.find_all("fieldset", class_="card card-list"):
            if "electronic resource" in result.get_text().lower():
                continue

            title_el = result.find("h3", class_="card-title")
            author_el = result.find("div", class_="card-text recdetails")
            if not title_el or not author_el:
                continue

            title = title_el.get_text().lower()
            author = author_el.find("span", class_="d-block")
            if not author:
                continue
            author = re.sub(r"\d+|author|-\s+", "", author.get_text().lower())

            if not self._title_match(book["title"], title) or not self._author_match(
                book["author"], author
            ):
                continue

            print("- " * 10)
            print("Searching for:", title, author)

            detail_url = self._get_detail_url(result)
            if not detail_url:
                continue

            record_id, item_id = self._extract_record_and_item_ids(detail_url)
            if not record_id or not item_id:
                continue

            detail_soup = self._fetch_record_details_soup(detail_url, book["title"])
            if detail_soup is None:
                continue

            availability_link = self._get_availability_link(detail_soup)
            if not availability_link:
                print("Availability button not found in record details page.")
                continue

            availability_url = self._build_availability_url(availability_link["href"])
            print(title, author, availability_url)

            return {"availability_url": availability_url}

        return None

    def get_holdings(self, url: str) -> list[dict]:
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            print(f"Holdings fetch failed for {url}: {e}")
            return []

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        # The holdings page is rendered as a table with a dedicated body. We read each
        # row and extract the branch, call number, collection, and status fields.
        table = soup.find("table", {"class": "table table-stacked"})
        if not table or not table.find("tbody"):
            return []

        holdings = []
        for row in table.find("tbody").find_all("tr"):
            location_el = row.find("td", {"data-caption": "Location"})
            if not location_el:
                continue

            call_number_el = row.find("td", {"data-caption": "Call number"})
            status_el = row.find("td", {"data-caption": "Status/Desc"})
            collection_el = row.find("td", {"data-caption": "Collection"})

            holdings.append(
                {
                    "location": location_el.get_text(strip=True),
                    "collection": (
                        collection_el.get_text(strip=True) if collection_el else ""
                    ),
                    "call_number": (
                        call_number_el.get_text(strip=True) if call_number_el else "N/A"
                    ),
                    "status": status_el.get_text(strip=True) if status_el else "",
                }
            )
        print(holdings)
        return holdings

    @staticmethod
    def _build_search_query(book: dict) -> str:
        parts = [book["title"], book["author"]]
        isbn = book.get("isbn", "").strip()
        if isbn:
            parts.append(isbn)
        return " ".join(parts)

    @staticmethod
    def _get_detail_url(result) -> str | None:
        # The live HTML stores the record-detail URL in a tab placeholder attribute.
        # This is more reliable than scanning every anchor, because the search card can
        # contain several links that are unrelated to the actual record detail page.
        for placeholder in result.select("div.tab-pane-url[data-tab-href]"):
            detail_href = placeholder.get("data-tab-href", "").strip()
            if not detail_href:
                continue

            normalized_href = detail_href.lower()
            if "/cgi-bin/spydus.exe/enq/opac/bibenq/" not in normalized_href:
                continue

            if "qry=" not in normalized_href:
                continue

            if "\\bk" in normalized_href or "%5cbk" in normalized_href:
                return urllib.parse.urljoin("https://wml.spydus.com", detail_href)

            if "\\eaud" not in normalized_href and "%5ceaud" not in normalized_href:
                return urllib.parse.urljoin("https://wml.spydus.com", detail_href)

        for link in result.find_all("a", href=True):
            href = link.get("href", "")
            if "/cgi-bin/spydus.exe/ENQ/OPAC/BIBENQ/" in href:
                return urllib.parse.urljoin("https://wml.spydus.com", href)

        return None

    @staticmethod
    def _extract_record_and_item_ids(detail_url: str) -> tuple[str | None, str | None]:
        # Spydus uses the path to identify the record and the query string to identify
        # the specific work or item. We parse both so we can ensure we have enough data
        # to request the correct holdings page.
        parsed_url = urllib.parse.urlparse(detail_url)
        path_parts = [part for part in parsed_url.path.split("/") if part]
        record_id = None
        if len(path_parts) >= 2 and path_parts[-2] == "BIBENQ":
            record_id = path_parts[-1] or None

        query_values = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True)
        qry = query_values.get("QRY", [""])[0]
        item_match = re.search(r"WRK01\\(\d+)", qry)
        item_id = item_match.group(1) if item_match else None

        return record_id, item_id

    def _fetch_record_details_soup(self, detail_url: str, title: str):
        # The detail page is fetched separately because the search results page itself is
        # not the place where the availability button is exposed.
        try:
            detail_response = requests.get(
                detail_url,
                headers={**HEADERS, **DETAIL_REQUEST_HEADERS},
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"Record details request failed for {title!r}: {e}")
            return None

        if detail_response.status_code != 200:
            print(
                f"Record details request failed for {title!r}, Status: {detail_response.status_code}"
            )
            return None

        return BeautifulSoup(detail_response.content, "html.parser")

    @staticmethod
    def _get_availability_link(detail_soup: BeautifulSoup):
        # The availability action is rendered as a button link on the record detail page.
        # We target the specific Spydus holdings endpoint rather than any generic anchor.
        return detail_soup.select_one(
            'a[href*="/cgi-bin/spydus.exe/XHLD/OPAC/BIBENQ/"][role="button"]'
        )

    @staticmethod
    def _build_availability_url(href: str) -> str:
        # The availability button uses a different URL structure from the final holdings
        # page. We rewrite the path into the form that the library site expects and add
        # the RECDISP parameter so the response is presented in the right view.
        parsed = urllib.parse.urlparse(href)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2:
            record_id, item_id = path_parts[-2:]
            path = f"/cgi-bin/spydus.exe/XHLD/OPAC/BIBENQ/{record_id}/{item_id}"
            query = urllib.parse.urlencode({"RECDISP": "REC"})
            return f"https://wml.spydus.com{path}?{query}"
        return f"https://wml.spydus.com{href}"

    @staticmethod
    def _extract_cover_url(result) -> str | None:
        image_tag = result.find_previous("div", class_="card-list-image-body")
        if not image_tag:
            return None
        img = image_tag.find("img", src=True)
        if not img:
            return None
        return img.get("longdesc")

    @staticmethod
    def _normalize_author(author: str) -> str:
        # The library and Goodreads data may represent the same author in slightly
        # different formats, such as "Cervantes Saavedra, Miguel de" versus
        # "Miguel de Cervantes Saavedra". We normalize by removing punctuation and
        # sorting tokens so the comparison is stable.
        author = author.lower().replace(",", "").replace(".", "")
        tokens = sorted(author.split())
        return " ".join(tokens)

    @staticmethod
    def _title_match(title1: str, title2: str) -> bool:
        # A simple substring check is usually enough here because the title text from the
        # library results is often a slightly expanded version of the Goodreads title.
        title1, title2 = title1.lower(), title2.lower()
        return title1 in title2 or title2 in title1

    @staticmethod
    def _author_match(author1: str, author2: str, threshold: float = 0.8) -> bool:
        # SequenceMatcher gives us a soft similarity score instead of an exact lexical
        # match, which helps with author-name variations without being too permissive.
        norm_author1 = LibraryScraper._normalize_author(author1)
        norm_author2 = LibraryScraper._normalize_author(author2)

        ratio = SequenceMatcher(None, norm_author1, norm_author2).ratio()
        return ratio >= threshold


def save_books_to_file(books, filename="books.json"):
    # Persist the enriched books so the data can be inspected or reused later without
    # having to rerun the scrape immediately.
    with open(filename, "w") as file:
        json.dump(books, file, indent=4)


def format_book_data(books, branch):
    # Build a plain-text summary for the selected branch so the availability data is
    # easier to read in the console or in a small report.
    formatted_books = []
    for book in books:
        formatted_book = f"Title: {book['title']}\n"
        formatted_book += f"Author: {book['author']}\n"
        formatted_book += f"Rating: {book['rating']}\n"
        for availability in book.get("availability", []):
            if availability["location"] == branch:
                formatted_book += (
                    f"Branch: {availability['location']}, "
                    f"Call Number: {availability['call_number']}, "
                    f"Status: {availability['status']}\n"
                )

        formatted_books.append(formatted_book.strip())

    return "\n\n".join(formatted_books)


if __name__ == "__main__":
    goodreads_scraper = GoodreadsScraper("151602501-apricot", "to-read")
    library = "Nunawading"
    books = goodreads_scraper.scrape_goodreads_list(page_limit=1)
    books_at_lib = LibraryScraper().books_at_branch(books, library)
    save_books_to_file(books_at_lib, "books_at_lib.json")
