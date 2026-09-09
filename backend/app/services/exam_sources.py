"""Curated TANDEM PDF/audio pairing. Never fetch arbitrary caller-supplied URLs."""

from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

PAGE = "https://www.tandem-muenchen.de/de/downloads.html"
CATALOG = {
    "b1": {
        "title": "telc Deutsch B1 – Modelltest",
        "pdf": "telc_ZD_B1_Modelltest.pdf",
        "audio": "Zertifikat-Deutsch-Modelltest.mp3",
    },
    "b2": {
        "title": "telc Deutsch B2 – Modelltest",
        "pdf": "telc_Deutsch_B2_Modelltest.pdf",
        "audio": "Deutsch_B2_Modelltest.mp3",
    },
    "c1": {
        "title": "telc C1 Hochschule – Übungstest 1",
        "pdf": "Deutsch_C1_Hochschule_Uebungstest_1.pdf",
        "audio": "Deutsch_C1_Hochschule_Uebungstest_1.mp3",
    },
}


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag != "a" or not href:
            return
        url = urljoin(PAGE, href)
        filename = parse_qs(urlsplit(url).query).get("f", [urlsplit(url).path.rsplit("/", 1)[-1]])[0]
        self.links[filename] = url


def download(url: str, limit: int) -> bytes:
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.tandem-muenchen.de" or parsed.port not in (None, 443):
            raise ValueError("Download verweist auf eine nicht unterstützte Quelle. Bitte Dateien manuell hochladen.")
        with httpx.stream("GET", url, timeout=90, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                continue
            response.raise_for_status()
            data = bytearray()
            for part in response.iter_bytes():
                data.extend(part)
                if len(data) > limit:
                    raise ValueError("Download ist zu groß. Bitte eine kleinere Datei hochladen.")
            return bytes(data)
    raise ValueError("Zu viele Weiterleitungen beim Download.")


def import_source(key: str, owner: str) -> dict[str, Any]:
    from app.services import exams

    if key not in CATALOG:
        raise ValueError("Unbekannte Prüfungsquelle.")
    parser = Links()
    parser.feed(download(PAGE, 2 * 1024 * 1024).decode("utf-8"))
    entry = CATALOG[key]
    try:
        pdf_url, audio_url = parser.links[entry["pdf"]], parser.links[entry["audio"]]
    except KeyError as exc:
        raise ValueError("Downloadlinks wurden verändert. Bitte PDF und MP3 manuell von TANDEM hochladen.") from exc
    pdf = download(pdf_url, exams.PDF_LIMIT)
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("TANDEM liefert keine PDF-Datei. Bitte die Dateien im Browser herunterladen und hochladen.")
    audio = download(audio_url, exams.AUDIO_LIMIT)
    return exams.create(owner, entry["pdf"], pdf, audio, PAGE)
