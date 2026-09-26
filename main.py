"""Extract the current electricity price from a provider page and write it to public/index.json."""

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, field_validator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OUTPUT_FILE = Path("public/index.json")
DEFAULT_MODEL = "gemini-3.8-flash"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
RETRY_STATUS_CODES = (408, 429, 500, 502, 503, 504)

PROMPT = """
Extract the electricity price (€/kWh) and the date from which it is valid from the
text of the energy provider's web page below.

CRITICAL CONVERSION RULES:
1. If the price is in Cents (e.g. '35 ct' or '35 Cent'), you MUST divide by 100 to get Euro.
2. A price of '14' is WRONG. It must be '0.14'.
3. The price per kWh is almost always between 0.05 and 0.40 Euro.
4. If your result is >= 1.0, you have made a unit error. Correct it.

PAGE_TEXT:
---
{page_text}
---
"""


class EnergyPriceInfo(BaseModel):
    """Schema for electricity price data with built-in validation."""

    price: float = Field(
        description="The electricity price in €/kWh. Convert Cents to Euro (e.g., 14.5ct -> 0.145)."
    )
    valid_from: datetime = Field(description="The start date of the price validity.")

    @field_validator("price")
    @classmethod
    def price_must_be_realistic(cls, v: float) -> float:
        if v >= 1.0:
            raise ValueError(
                f"Extracted price {v} is too high (>1.00€). Likely a Cent-to-Euro conversion error."
            )
        if v <= 0:
            raise ValueError(f"Extracted price {v} must be positive.")
        return v


@dataclass(frozen=True)
class Settings:
    api_key: str
    provider_url: str
    model: str

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        provider_url = os.environ.get("ENERGY_PROVIDER_URL", "").strip()
        missing = [
            name
            for name, value in (
                ("GOOGLE_API_KEY", api_key),
                ("ENERGY_PROVIDER_URL", provider_url),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing environment variable(s): {', '.join(missing)}")
        return cls(
            api_key, provider_url, os.environ.get("GEMINI_MODEL_NAME") or DEFAULT_MODEL
        )

    def secrets(self) -> list[str]:
        """Every fragment of the secrets that could show up on its own in an error message."""
        parts = urlsplit(self.provider_url)
        host = parts.hostname or ""
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        candidates = [
            self.api_key,
            self.provider_url,
            host,
            host.removeprefix("www."),
            path,
            path.lstrip("/"),
        ]
        # Skip trivial fragments like "/" so redaction doesn't mangle unrelated text.
        return sorted({c for c in candidates if len(c) > 2}, key=len, reverse=True)


def redact(message: str, secrets: list[str]) -> str:
    for secret in secrets:
        message = message.replace(secret, "***")
    return message


class _TextExtractor(HTMLParser):
    """Collects the visible text of a page, dropping scripts, styles and other non-content markup."""

    SKIP = {"script", "style", "noscript", "svg", "template", "head", "iframe"}
    BLOCK = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "table",
    }

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def fetch_page_text(url: str) -> str:
    retry = Retry(
        total=4,
        backoff_factor=5,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods={"GET"},
    )
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        response = session.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=(15, 30)
        )
        response.raise_for_status()

    text = html_to_text(response.text)
    if not text:
        raise RuntimeError("Provider page contained no readable text.")
    return text


def extract_price(page_text: str, settings: Settings) -> EnergyPriceInfo:
    client = genai.Client(
        api_key=settings.api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                attempts=5,
                initial_delay=5,
                http_status_codes=list(RETRY_STATUS_CODES),
            )
        ),
    )

    response = client.models.generate_content(
        model=settings.model,
        contents=PROMPT.format(page_text=page_text),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EnergyPriceInfo,
            temperature=0.0,
        ),
    )
    if not response.text:
        raise RuntimeError("No response text received from the model.")
    return EnergyPriceInfo.model_validate_json(response.text)


def write_output(price_info: EnergyPriceInfo) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(price_info.model_dump(mode="json"), indent=4), encoding="utf-8"
    )


def run(settings: Settings) -> EnergyPriceInfo:
    price_info = extract_price(fetch_page_text(settings.provider_url), settings)
    write_output(price_info)
    return price_info


def main() -> None:
    try:
        settings = Settings.from_env()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        price_info = run(settings)
    except Exception as e:
        print(
            f"Error: {redact(f'{type(e).__name__}: {e}', settings.secrets())}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Success: {price_info.price} €/kWh (valid from {price_info.valid_from.date()})"
    )


if __name__ == "__main__":
    main()
