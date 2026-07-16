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


class GoodreadsScraper:
    def __init__(self, user, list_name) -> None:
        self.user = user
        self.list_name = list_name
        self.books = []
        self.rss_base_url = f"https://www.goodreads.com/review/list_rss/{self.user}"

    def fetch_rss_page(self, page):
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
        if not items:
            print(f"Page {page}: No books found.")
            return False

        for item in items:
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
        self.workers = workers

    def books_at_branch(self, books: list[dict], branch: str) -> list[dict]:
        """Look up each book in parallel and return copies available at the branch."""
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            enriched = executor.map(lambda book: self.enrich_book(book, branch), books)
        return [book for book in enriched if book is not None]

    def enrich_book(self, book: dict, branch: str) -> dict | None:
        match = self.search_catalog(book)

        if match is None:
            return None
        print("library match:", match)

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
        query = self._build_search_query(book)
        params = {
            "ENTRY": query,
            "ENTRY_NAME": "BS",
            "ENTRY_TYPE": "K",
            "SORTS": "SQL_REL_BIB",
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

############# THIS IS THE ISSUE: CANNOT FIND CORRECT FIRST SELECTOR (IN PLACE OF div#BK-pane-84405260-url)
                # WITHOUT THIS SELECTOR, IT WILL FOCUS ON THE RESERVATION BUTTON INSTEAD
            book_link = result.select_one("div#BK-pane-84405260-url div.card-text.availability a[href]")

            print("Book link found:", book_link)
            if not book_link:
                continue


            availability_url = f"https://wml.spydus.com{book_link['href']}"

            print(title, author, availability_url)

            # cover_url = self._extract_cover_url(result)
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
        # Handle "Lname, Fname" vs "Fname Lname" by just extracting tokens and sorting them
        author = author.lower().replace(",", "").replace(".", "")
        tokens = sorted(author.split())
        return " ".join(tokens)

    @staticmethod
    def _title_match(title1: str, title2: str) -> bool:
        title1, title2 = title1.lower(), title2.lower()
        return title1 in title2 or title2 in title1

    @staticmethod
    def _author_match(author1: str, author2: str, threshold: float = 0.8) -> bool:
        norm_author1 = LibraryScraper._normalize_author(author1)
        norm_author2 = LibraryScraper._normalize_author(author2)

        ratio = SequenceMatcher(None, norm_author1, norm_author2).ratio()
        return ratio >= threshold


def save_books_to_file(books, filename="books.json"):
    with open(filename, "w") as file:
        json.dump(books, file, indent=4)


def format_book_data(books, branch):
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
