# books_download

Scrapes a reading list from [Intégrer Sciences Po](https://www.integrersciencespo.fr/)
and downloads the matching public-domain ebooks from
[Project Gutenberg](https://www.gutenberg.org/).

For each book found on the source pages, the script searches Project Gutenberg,
takes the top match, and downloads it — preferring EPUB and falling back to
plain text when EPUB isn't available.

## How it works

1. **Scrape** – `get_books_from_page` flattens each source page to text and
   extracts `Author – Title – Date` entries with a single global regex
   (the entries are concatenated into large blocks mixed with prose, so they
   can't be parsed one HTML element at a time). Duplicates are removed.
2. **Search** – `search_and_download_gutenberg` normalizes the query (strips
   accents and typographic punctuation, since Gutenberg's search doesn't match
   `Homère - L'Odyssée` but does match `Homere L Odyssee`), then tries
   progressively looser queries: full `author title`, then author-only, then
   title-only.
3. **Download** – it fetches the top match from Project Gutenberg's predictable
   per-book file URLs, validating the response content-type so a missing
   format is never saved as a bogus file. EPUB is preferred, with TXT as a
   fallback.
4. **Report** – books that couldn't be downloaded are written to
   `not_downloaded.txt` after the run.

Downloaded files are saved to `gutenberg_downloads/`.

> **Note:** the script talks to Project Gutenberg directly rather than through
> the Gutendex API, which now sits behind a Cloudflare challenge that a plain
> HTTP client can't pass.

## Requirements

- Python 3.9+
- [`requests`](https://pypi.org/project/requests/)
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/)

## Usage

A `Makefile` handles the virtual environment, dependencies, and running:

```sh
make install   # create .venv and install dependencies
make run        # run the downloader inside the venv (installs deps if needed)
make clean      # remove the virtual environment
```

Or manually:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python dl_100_books.py
```

## Configuration

Both are optional environment variables:

- `GUTENBERG_BASE` – base URL for Project Gutenberg
  (default `https://www.gutenberg.org`).

## Output

- `gutenberg_downloads/` – the downloaded `.epub` / `.txt` files.
- `not_downloaded.txt` – list of books that had no Gutenberg match or no
  downloadable format (not created when every book succeeds).

## Known limitations

- The source list uses French author/title spellings. Project Gutenberg's
  catalog is largely English-centric, so some entries (e.g. "Ésope" rather than
  "Aesop") won't match and are reported in `not_downloaded.txt`.
- Entries without a trailing date (e.g. "Les Mille et Une Nuits") are skipped
  by the parser, which requires the `Author – Title – Date` shape to avoid
  pulling in surrounding prose.
