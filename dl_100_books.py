import os
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup


def normalize_query(text: str) -> str:
    """Normalize a scraped author/title string for Project Gutenberg search.

    Gutenberg's search does not match queries that contain accented
    characters or typographic punctuation (e.g. "Homère - L'Odyssée" returns
    nothing, while "Homere L Odyssee" matches). This strips diacritics,
    converts curly quotes/dashes to ASCII, drops punctuation, and collapses
    whitespace.
    """
    # Decompose accents (é -> e + combining mark) and drop the marks.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Replace any remaining non-alphanumeric characters with spaces
    # (handles curly apostrophes, em dashes, hyphens, punctuation).
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_books_from_page(url: str):
    """Scrapes book titles and authors from an Intégrer Sciences Po page.

    On this site the entries are concatenated into large text blocks (with no
    clean per-book HTML separation) and follow the pattern:

        "Author – Title – Date : comment"

    using an em dash (–), e.g. "Homère – L'Odyssée – VIIIe siècle avant J-C. :".
    Several entries are glued together with no separator between the end of one
    comment and the next author, so we parse with a single global regex over the
    whole page text rather than per-element.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"[!] Failed to load page: {url}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # Collapse the whole page into a single text string.
    full_text = soup.get_text(separator=" ")
    full_text = re.sub(r"\s+", " ", full_text)

    # Match "Author – Title – Date :" entries.
    #   - Author: starts after a sentence boundary (». ! ? : or start) so the
    #     preceding advice paragraph isn't swallowed into the author field. We
    #     keep it short (no em dash, bounded length) to avoid grabbing prose.
    #   - Title: text up to the next em dash (the date separator).
    # The date/comment after the second em dash is ignored.
    pattern = re.compile(
        r"(?:^|[.:!?»)])\s*"          # sentence boundary before the author
        r"([A-ZÀ-ÖØ-Þ][^–»:]{1,60}?)"  # author (starts uppercase, bounded)
        r"\s*–\s*"                     # em dash separator
        r"([^–»:]{1,120}?)"            # title
        r"\s*–\s*"                     # em dash before the date
    )

    books = []
    seen = set()
    for match in pattern.finditer(full_text):
        author = match.group(1).strip()
        title = match.group(2).strip()
        key = (author.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        books.append({"author": author, "title": title})

    return books


# Base URL for Project Gutenberg. We talk to Project Gutenberg directly
# rather than through the Gutendex API: as of mid-2026 gutendex.com sits
# behind a Cloudflare JS challenge that a plain HTTP client can't pass
# (requests hang or return 403), and the community mirrors are unreliable.
# Gutenberg's own search page and ebook file URLs are stable and scriptable.
GUTENBERG_BASE = os.environ.get(
    "GUTENBERG_BASE", "https://www.gutenberg.org"
).rstrip("/")

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _http_get(url: str, timeout: int, params: dict | None = None):
    """GET with a shared User-Agent and up to 3 retries on transient errors."""
    for attempt in range(3):
        try:
            res = requests.get(
                url, params=params, headers=_HTTP_HEADERS, timeout=timeout
            )
            res.raise_for_status()
            return res
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                print(f"  [~] Retry {attempt + 1}/2 after error: {e}")
                time.sleep(2 * (attempt + 1))
            else:
                raise


def _search_gutenberg_ids(query: str):
    """Return a list of Project Gutenberg ebook IDs matching the query.

    Scrapes the public search page (no API key, no Cloudflare challenge).
    IDs are returned in Gutenberg's own relevance/popularity order, with
    duplicates removed and original order preserved.
    """
    res = _http_get(
        f"{GUTENBERG_BASE}/ebooks/search/", timeout=30, params={"query": query}
    )
    ids = re.findall(r"/ebooks/(\d+)", res.text)
    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for book_id in ids:
        if book_id not in seen:
            seen.add(book_id)
            unique.append(book_id)
    return unique


# Per-format download info for a Gutenberg ebook id: URL template, file
# extension, and the content-type substring we expect so we can detect a
# format that isn't actually available (Gutenberg may answer a missing
# format with an HTML error page and HTTP 200).
_FORMAT_SPECS = {
    "epub": {
        "url": "{base}/ebooks/{id}.epub.images",
        "ext": "epub",
        "content_type": "epub",
    },
    "txt": {
        "url": "{base}/ebooks/{id}.txt.utf-8",
        "ext": "txt",
        "content_type": "text/plain",
    },
}


def _download_format(gutenberg_id: str, fmt: str, dest: Path) -> bool:
    """Download one format for a book id, validating the content type.

    Returns True on success. Returns False (without raising) when the format
    isn't available, so the caller can try the next preferred format.
    """
    spec = _FORMAT_SPECS[fmt]
    url = spec["url"].format(base=GUTENBERG_BASE, id=gutenberg_id)
    try:
        res = _http_get(url, timeout=60)
    except requests.exceptions.RequestException:
        return False

    content_type = res.headers.get("Content-Type", "").lower()
    # If the server returned an HTML page, this format isn't available.
    if "html" in content_type or spec["content_type"] not in content_type:
        return False

    dest.write_bytes(res.content)
    return True


def search_and_download_gutenberg(
    author: str, title: str, download_dir: Path, fmt: str = "epub"
):
    """Searches Project Gutenberg and downloads the top matching ebook.

    :param fmt: preferred format, 'epub' or 'txt'. The other format is used
        as a fallback when the preferred one isn't available for a book.
    """
    # Preference order: the requested format first, then the other one.
    fmt = fmt if fmt in _FORMAT_SPECS else "epub"
    format_order = [fmt] + [f for f in ("epub", "txt") if f != fmt]

    # Try progressively looser queries. Full "author title" is most precise,
    # but French titles often don't match Gutenberg's (frequently English)
    # cataloging, so fall back to author-only, then title-only.
    candidates = []
    for raw in (f"{author} {title}", author, title):
        q = normalize_query(raw)
        if q and q not in candidates:
            candidates.append(q)

    try:
        ids = []
        used_query = None
        for q in candidates:
            ids = _search_gutenberg_ids(q)
            if ids:
                used_query = q
                break

        if not ids:
            print(f"  [x] No Project Gutenberg match found for: {author} - {title}")
            return False

        if used_query != candidates[0]:
            print(f"  [~] Matched via looser query: '{used_query}'")

        # Take the top match.
        gutenberg_id = ids[0]
        clean_title = re.sub(r'[\\/*?:"<>|]', "", f"{author}_{title}").strip()

        # Try the preferred format first, then fall back to the other.
        for candidate_fmt in format_order:
            extension = _FORMAT_SPECS[candidate_fmt]["ext"]
            file_path = download_dir / f"{clean_title}.{extension}"
            if _download_format(gutenberg_id, candidate_fmt, file_path):
                note = (
                    "" if candidate_fmt == fmt else f" (fell back to {candidate_fmt})"
                )
                print(
                    f"  [✓] Downloaded (ID {gutenberg_id}){note}: {file_path.name}"
                )
                return True

        print(
            f"  [!] No downloadable format found for Gutenberg ID {gutenberg_id} "
            f"({author} - {title})"
        )
        return False

    except Exception as e:
        print(f"  [!] Error processing {title}: {e}")
        return False


def main():
    # 1. Base URLs for the pages you want to scrape
    # Page 1: https://www.integrersciencespo.fr/livres-1
    # Page 2: https://www.integrersciencespo.fr/livres-2 (etc.)
    pages_to_scrape = [
        "https://www.integrersciencespo.fr/livres-1",
        "https://www.integrersciencespo.fr/livres-2",
        "https://www.integrersciencespo.fr/livres-3",
        "https://www.integrersciencespo.fr/livres-4",
        "https://www.integrersciencespo.fr/livres-5",
    ]

    download_dir = Path("./gutenberg_downloads")
    download_dir.mkdir(exist_ok=True)

    all_books = []

    # 2. Extract books from all specified pages
    for page_url in pages_to_scrape:
        print(f"\nScanning page: {page_url}")
        books = get_books_from_page(page_url)
        print(f"Found {len(books)} books on this page.")
        all_books.extend(books)

    print(f"\nTotal books extracted across pages: {len(all_books)}")

    # 3. Search Project Gutenberg and download, tracking failures
    print("\nStarting downloads from Project Gutenberg...\n")
    not_downloaded = []
    for i, book in enumerate(all_books, 1):
        print(
            f"[{i}/{len(all_books)}] Searching: {book['author']} - {book['title']}"
        )
        success = search_and_download_gutenberg(
            book["author"], book["title"], download_dir, fmt="epub"
        )
        if not success:
            not_downloaded.append(book)
        time.sleep(1)  # Respect API rate limits

    # 4. Write a report of the books that could not be downloaded
    downloaded_count = len(all_books) - len(not_downloaded)
    print(
        f"\nDone. Downloaded {downloaded_count}/{len(all_books)} books; "
        f"{len(not_downloaded)} could not be downloaded."
    )

    report_path = Path("./not_downloaded.txt")
    if not_downloaded:
        lines = [
            f"{book['author']} - {book['title']}" for book in not_downloaded
        ]
        report_path.write_text(
            f"# {len(not_downloaded)} book(s) not downloaded\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
        print(f"List of books not downloaded written to: {report_path}")
    else:
        # No failures: remove any stale report from a previous run.
        if report_path.exists():
            report_path.unlink()
        print("All books downloaded successfully.")


if __name__ == "__main__":
    main()