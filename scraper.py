import requests
from bs4 import BeautifulSoup
import json
import urllib.parse


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


class GoodreadsScraper:
    def __init__(self, user, list_name) -> None:
        self.user = user
        self.list_name = list_name
        self.books = []

    def scrape_goodreads_list(self, page=1, page_limit=None, chosen_library=None):
        """
        Scrapes the entire given goodreads list.
        """
        base_url = f"https://www.goodreads.com/review/list/{self.user}"

        # only desktop html targetted for now
        while True:
            list_url = f"{base_url}?shelf={self.list_name}&page={page}"
            response = requests.get(list_url, headers=headers)

            if response.status_code != 200 or page == (
                page_limit + 1
            ):  # add 1 so that the number of pages scraped ends up = the page_limit
                print(f"Page limit of {(page_limit + 1)} pages reached")
                # print(response)
                break

            soup = BeautifulSoup(response.content, "html.parser")
            if bool(soup.find("html", {"class": "desktop"})):
                print("DESKTOP YES")
            else:
                print("MOBILE YES")
            # Check if the shelf is empty
            empty_shelf = soup.find("p", {"class": "empty"})
            if empty_shelf:
                print("The shelf is empty.")
                break

            rows = soup.find_all("tr", {"class": "bookalike review"})

            if not rows:
                print("No more books found.")
                break

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
                availability = lib_scraper.check_local_library()
                if availability:
                    book["availability"] = availability
                self.books.append(book)

            print(f"Scraped page {page}, found {len(rows)} books.")
            page += 1

        return self.books

    def find_at(self, chosen_library):
        """
        Find which books from your list are in your chosen library/ies.
        """
        # chosen_library = "Nunawading"
        books_in_library = []

        # return books_in_library
        print(f"Books available at {chosen_library}: ")
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
            # print("goodreads:", self.book["title"].lower(), "| library:", title)
            # print("goodreads:", self.book["author"].lower(), "| library:", author)

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
                return self.get_book_availability(availability_url)
        return {}

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
        return []


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


# if __name__ == "__main__":
#     goodreads_scraper = GoodreadsScraper("151602501-apricot", "to-read")
#     library = "Nunawading"
#     books = goodreads_scraper.scrape_goodreads_list(2, library)
#     save_books_to_file(books)
#     books_at_lib = goodreads_scraper.find_at(library)
#     save_books_to_file(books_at_lib, "books_at_lib.json")
