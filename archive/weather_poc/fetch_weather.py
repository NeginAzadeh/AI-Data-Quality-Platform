"""Fetch hourly temperatures for Turin and save the API response as JSON."""

import json
from pathlib import Path

import requests


def main():
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": 45.0703,
        "longitude": 7.6869,
        "start_date": "2026-09-01",
        "end_date": "2026-09-03",
        "hourly": "temperature_2m",
        "temperature_unit": "celsius",
        "timezone": "Europe/Rome",
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        raise SystemExit("The API request timed out. Please try again.")
    except requests.exceptions.RequestException as error:
        raise SystemExit(f"Could not fetch weather data: {error}")
    except ValueError:
        raise SystemExit("The API response was not valid JSON.")

    # Resolve the project root above archive/weather_poc/.
    output_path = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "raw"
        / "turin_temperature_2026-09-01_to_2026-09-03.json"
    )

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
    except OSError as error:
        raise SystemExit(f"Could not save the JSON file: {error}")

    print(f"Weather data saved to: {output_path}")


if __name__ == "__main__":
    main()
