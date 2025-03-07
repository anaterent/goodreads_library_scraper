import requests
from bs4 import BeautifulSoup
import json
import urllib.parse
import aiohttp
import asyncio


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


class GoodreadsScraper:
    def __init__(self, user, list_name) -> None:
        self.user = user
        self.list_name = list_name
        self.books = []
        self.base_url = f"https://www.goodreads.com/review/list/{self.user}"

    async def fetch_page(self, session, page):
        list_url = f"{self.base_url}?shelf={self.list_name}&page={page}"

        try:
            async with session.get("https://www.google.com") as test_response:
                print(f"Test request successful: {test_response.status}")

            async with session.get(list_url, headers=headers) as response:
                if response.status != 200:
                    print(f"Page {page} fetch failed, Status: {response.status}")
                    return None
                return await response.text()
        except aiohttp.ClientError as e:
            print(f"Request error on page {page}: {e}")
            return None

    async def scrape(self, start_page, page_limit, chosen_library=None):
        last_page = start_page + page_limit
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20)
        ) as session:
            tasks = [
                self.fetch_page(session, page) for page in range(start_page, last_page)
            ]
            pages_content = await asyncio.gather(*tasks)

        for page_num, html in enumerate(pages_content, start=start_page):
            if html:
                if not self.parse_page(html, page_num, chosen_library):
                    break  # Stop scraping if the shelf is empty

    def parse_page(self, html, page, chosen_library) -> list:
        soup = BeautifulSoup(html, "lxml")

        if soup.find("html", {"class": "desktop"}):
            print(f"Page {page}: DESKTOP YES")
        else:
            print(f"Page {page}: MOBILE YES")

        empty_shelf = soup.find("p", {"class": "empty"})
        if empty_shelf:
            print(f"Page {page}: The shelf is empty.")
            return False  # Stop further processing

        rows = soup.find_all("tr", {"class": "bookalike review"})
        if not rows:
            print("No more books found.")
            return False

        for row in rows:
            title = (
                row.find("td", {"class": "field title"})
                .find("div", {"class": "value"})
                .get_text(strip=True)
            )
            author = (
                row.find("td", {"class": "field author"})
                .find("div", {"class": "value"})
                .get_text(strip=True)
            )
            rating = (
                row.find("td", {"class": "field avg_rating"})
                .find("div", {"class": "value"})
                .get_text(strip=True)
            )
            book = {"title": title, "author": author, "rating": rating}
            lib_scraper = LibraryScraper(book, chosen_library)
            result = lib_scraper.check_local_library()
            if result is not None:
                availability, img_url = result
                if availability:
                    book["availability"] = availability
                if img_url:
                    book["img_url"] = img_url
            self.books.append(book)

        print(f"Page {page}: Found {len(rows)} books.")
        # page += 1

        return self.books  # Continue scraping

    def scrape_goodreads_list(
        self, page: int = 1, page_limit: float | int = float("inf"), chosen_library=None
    ) -> list:
        """
        Scrapes the entire given goodreads list.
        """
        return asyncio.run(self.scrape(page, page_limit, chosen_library))

    def find_at(self, chosen_library: str):
        """
        Find which books from your list are in your chosen library
        """
        # eg. chosen_library = "Nunawading"
        books_in_library = []

        # return books_in_library
        for book in self.books:
            if "availability" in book:
                for availability_info in book["availability"]:
                    if availability_info["location"] == chosen_library:
                        books_in_library.append(book)
                        break
        # print(format_book_data(books_in_library, chosen_library))
        return books_in_library


class LibraryScraper:

    def __init__(self, book, chosen_library):
        self.book = book
        self.library = chosen_library

    def check_local_library(self):
        base_url = "https://wml.spydus.com/cgi-bin/spydus.exe/ENQ/WPAC/BIBENQ"
        query = f'{self.book["title"]} {self.book["author"]}'
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

        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            print(f"Failed to retrieve the page")
            print(response)

        soup = BeautifulSoup(response.content, "html.parser")

        # find first book matching title, ignore electronic copies
        book_results = soup.find_all("div", class_="card-record-body")
        for book_result in book_results:

            # Skip electronic resources
            if "electronic resource" in book_result.get_text().lower():
                continue

            title = book_result.find("h2", class_="card-title").get_text().lower()
            author = (
                book_result.find("div", class_="card-text recdetails")
                .find("span", class_="d-block")
                .get_text()
                .lower()
            )

            # Normalize and compare titles and authors
            if not self.is_title_match(
                self.book["title"], title
            ) or not self.is_author_match(self.book["author"], author):
                continue

            book_link = book_result.find("a", href=True)
            if book_link:
                href = book_link["href"]
                book_details_url = f"https://wml.spydus.com{href}"
                availability_url = book_details_url.replace(
                    "FULL/WPAC/BIBENQ", "XHLD/WPAC/BIBENQ"
                )

            # Extract book image
            image_tag = book_result.find_previous("div", class_="card-list-image-body")
            image_url = None
            if image_tag:
                img = image_tag.find("img", src=True)
                if img:
                    image_url = img["longdesc"]

            availability = self.get_book_availability(availability_url)
            return availability, image_url
        return None

    def is_title_match(self, title1, title2):
        """
        Check if the two titles are a good enough match.
        """
        title1, title2 = title1.lower(), title2.lower()
        return title1 in title2 or title2 in title1

    def is_author_match(self, author1, author2):
        """
        Check if the two authors are a good enough match.
        """
        author1, author2 = author1.lower(), author2.lower()
        return author1 in author2 or author2 in author1
        # check availability

    def get_book_availability(self, url):
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            rows = (
                soup.find("table", {"class": "table table-stacked"})
                .find("tbody")
                .find_all("tr")
            )
            availability = []
            for row in rows:
                location = row.find("td", {"data-caption": "Location"}).get_text(
                    strip=True
                )
                collection = row.find("td", {"data-caption": "Collection"}).get_text(
                    strip=True
                )
                call_number = row.find("td", {"data-caption": "Call number"})
                call_number_text = (
                    call_number.get_text(strip=True) if call_number else "N/A"
                )

                status = row.find("td", {"data-caption": "Status/Desc"}).get_text(
                    strip=True
                )

                availability.append(
                    {
                        "location": location,
                        "collection": collection,
                        "call_number": call_number_text,
                        "status": status,
                    }
                )
            return availability
        return None


def save_books_to_file(books, filename="books.json"):
    with open(filename, "w") as file:
        json.dump(books, file, indent=4)


def format_book_data(books, chosen_library):
    formatted_books = []
    for book in books:
        formatted_book = f"Title: {book['title']}\n"
        formatted_book += f"Author: {book['author']}\n"
        formatted_book += f"Rating: {book['rating']}\n"
        for availability in book["availability"]:
            if availability["location"] == chosen_library:
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
    books = goodreads_scraper.scrape_goodreads_list(
        page_limit=5, chosen_library=library
    )
    books_at_lib = goodreads_scraper.find_at(library)
    save_books_to_file(books_at_lib, "books_at_lib.json")
