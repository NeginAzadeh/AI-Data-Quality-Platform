"""Load Turin's hourly temperatures from the saved JSON into DuckDB."""

import json
from pathlib import Path

import duckdb


def main():
    # Keep using the original data directory at the project root.
    project_dir = Path(__file__).resolve().parents[2]
    input_path = (
        project_dir / "data" / "raw"
        / "turin_temperature_2026-09-01_to_2026-09-03.json"
    )
    database_path = project_dir / "data" / "processed" / "weather.duckdb"

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    timestamps = data["hourly"]["time"]
    temperatures = data["hourly"]["temperature_2m"]

    # Check before zip(), which otherwise stops at the shorter list.
    if len(timestamps) != len(temperatures):
        raise SystemExit(
            "Cannot load weather data: timestamp and temperature lists "
            f"have different lengths ({len(timestamps)} and {len(temperatures)})."
        )

    records = list(zip(timestamps, temperatures))
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(database_path)) as connection:
        # These timestamps represent Turin local time (Europe/Rome).
        connection.execute("""
            CREATE TABLE IF NOT EXISTS weather_hourly (
                timestamp TIMESTAMP PRIMARY KEY,
                temperature_c DOUBLE
            )
        """)

        # The primary key allows only one row per timestamp.
        if records:
            connection.executemany("""
                INSERT INTO weather_hourly (timestamp, temperature_c)
                VALUES (?, ?)
                ON CONFLICT (timestamp) DO NOTHING
            """, records)

        total_rows = connection.execute("""
            SELECT COUNT(*) FROM weather_hourly
        """).fetchone()[0]

        first_five = connection.execute("""
            SELECT timestamp, temperature_c
            FROM weather_hourly
            ORDER BY timestamp
            LIMIT 5
        """).fetchall()

        print(f"Database: {database_path}")
        print(f"Total rows: {total_rows}")
        print("First five records (Turin local time):")
        print("timestamp | temperature_c")
        for timestamp, temperature in first_five:
            print(f"{timestamp} | {temperature}")


if __name__ == "__main__":
    main()
