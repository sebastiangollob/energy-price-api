# Energy Price Extractor

This project automates the extraction of current electricity prices from a provider's website. It uses Google's Gemini AI to intelligently parse the HTML content, identifying the price per kWh and its validity date, and exports the data as a clean JSON file.

This is particularly useful for tracking energy costs programmatically or integrating dynamic pricing into home automation dashboards.

## How it Works

1. Retrieves the HTML from a specified energy provider URL.
2. Strips the page down to its visible text and uses Google's Gemini to extract the electricity price.
3. Extracted data is validated using `Pydantic` to ensure prices are within a realistic range.
4. The result is saved to `public/index.json` for publishing via GitHub Pages or other static site hosts.

## Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv)
- A Google Cloud API Key with access to Gemini models (there is a free tier available).

## Setup & Installation

1.  Clone the repository
2.  Install dependencies using `uv sync`

## Configuration

You need to set the following environment variables. You can set them in your shell or use a `.env` file.

1.  Copy the template file:
    ```bash
    cp .env.template .env
    ```
2.  Edit `.env` and fill in your values:
    - `GOOGLE_API_KEY`: Your API key for Google Gemini.
    - `ENERGY_PROVIDER_URL`: The URL of the webpage containing the energy price.
    - `GEMINI_MODEL_NAME` (optional): Overrides the default Gemini model.

## Running Locally

To run the script using `uv` and load the environment variables from your `.env` file:

```bash
uv run --env-file .env main.py
```

## Output

Upon success, the script creates a file at `public/index.json` with the following structure:

```json
{
  "price": 0.145,
  "valid_from": "2024-01-01T00:00:00"
}
```

- `price`: The electricity price in €/kWh.
- `valid_from`: The ISO 8601 formatted date when this price is valid.
