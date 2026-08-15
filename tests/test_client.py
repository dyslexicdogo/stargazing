"""Tests for client.py using aioresponses to mock Open-Meteo.

Uses the regex URL matching pattern from PROJECT_PRINCIPLES.md --
aioresponses needs re.compile(rf"^{re.escape(BASE_URL)}.*$") because
plain string matching breaks once query parameters are attached.
"""

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.stargazing.client import (
    BASE_URL,
    OpenMeteoClient,
    OpenMeteoError,
)

URL_PATTERN = re.compile(rf"^{re.escape(BASE_URL)}.*$")


def make_payload(hours: int = 3) -> dict:
    """A small, valid Open-Meteo-shaped response covering `hours` hours."""
    times = [f"2026-08-14T{20 + i:02d}:00" for i in range(hours)]
    return {
        "latitude": 57.48,
        "longitude": -4.22,
        "hourly": {
            "time": times,
            "cloud_cover_low": [5.0, 20.0, 60.0][:hours],
            "cloud_cover_mid": [10.0, 25.0, 55.0][:hours],
            "cloud_cover_high": [15.0, 30.0, 45.0][:hours],
            "temperature_2m": [9.0, 8.5, 8.0][:hours],
            "dew_point_2m": [4.0, 4.5, 6.0][:hours],
            "visibility": [24000.0, 18000.0, 6000.0][:hours],
            "wind_speed_300hPa": [36.0, 72.0, 144.0][:hours],  # km/h
            "precipitation_probability": [0.0, 10.0, 60.0][:hours],
            "wind_speed_10m": [8.0, 12.0, 30.0][:hours],
        },
    }


@pytest.mark.asyncio
async def test_parses_valid_response_into_readings():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=make_payload(hours=3))

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            readings = await client.async_get_hourly_forecast(
                latitude=57.48, longitude=-4.22
            )

    assert len(readings) == 3
    first = readings[0]
    assert first.low_cloud_cover == 5.0
    assert first.mid_cloud_cover == 10.0
    assert first.high_cloud_cover == 15.0
    assert first.temperature == 9.0
    assert first.dew_point == 4.0
    assert first.visibility == 24000.0
    assert first.precipitation_probability == 0.0
    assert first.wind_speed == 8.0


@pytest.mark.asyncio
async def test_jet_stream_wind_converted_from_kmh_to_ms():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=make_payload(hours=1))

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            readings = await client.async_get_hourly_forecast(
                latitude=57.48, longitude=-4.22
            )

    # 36.0 km/h -> 10.0 m/s
    assert readings[0].jet_stream_wind_speed == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_time_is_parsed_as_datetime():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=make_payload(hours=1))

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            readings = await client.async_get_hourly_forecast(
                latitude=57.48, longitude=-4.22
            )

    assert readings[0].time.hour == 20
    assert readings[0].time.day == 14


@pytest.mark.asyncio
async def test_request_includes_all_expected_hourly_variables():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=make_payload(hours=1))

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            await client.async_get_hourly_forecast(latitude=57.48, longitude=-4.22)

        request = mocked.requests[("GET", mocked.requests and list(mocked.requests.keys())[0][1])]
        called_url = str(request[0].kwargs["params"]["hourly"])
        for variable in (
            "cloud_cover_low",
            "cloud_cover_mid",
            "cloud_cover_high",
            "temperature_2m",
            "dew_point_2m",
            "visibility",
            "wind_speed_300hPa",
            "precipitation_probability",
            "wind_speed_10m",
        ):
            assert variable in called_url


@pytest.mark.asyncio
async def test_non_200_status_raises_open_meteo_error():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, status=400, body="Bad Request")

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            with pytest.raises(OpenMeteoError):
                await client.async_get_hourly_forecast(latitude=57.48, longitude=-4.22)


@pytest.mark.asyncio
async def test_api_error_payload_raises_open_meteo_error():
    with aioresponses() as mocked:
        mocked.get(
            URL_PATTERN,
            payload={"error": True, "reason": "Cannot initialize latitude"},
        )

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            with pytest.raises(OpenMeteoError, match="Cannot initialize latitude"):
                await client.async_get_hourly_forecast(latitude=999.0, longitude=0.0)


@pytest.mark.asyncio
async def test_missing_expected_field_raises_open_meteo_error():
    payload = make_payload(hours=1)
    del payload["hourly"]["wind_speed_300hPa"]

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=payload)

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            with pytest.raises(OpenMeteoError, match="missing expected field"):
                await client.async_get_hourly_forecast(latitude=57.48, longitude=-4.22)


@pytest.mark.asyncio
async def test_mismatched_array_lengths_raises_open_meteo_error():
    payload = make_payload(hours=3)
    payload["hourly"]["temperature_2m"] = [9.0, 8.5]  # one short

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=payload)

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            with pytest.raises(OpenMeteoError, match="mismatched lengths"):
                await client.async_get_hourly_forecast(latitude=57.48, longitude=-4.22)


@pytest.mark.asyncio
async def test_connection_failure_raises_open_meteo_error():
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, exception=aiohttp.ClientConnectionError("boom"))

        async with aiohttp.ClientSession() as session:
            client = OpenMeteoClient(session)
            with pytest.raises(OpenMeteoError, match="Failed to reach Open-Meteo"):
                await client.async_get_hourly_forecast(latitude=57.48, longitude=-4.22)