"""Weather module — current conditions and 24-hour forecast via OpenWeatherMap."""

from __future__ import annotations

import datetime
import requests

from .base import PebbleModule

_CURRENT_URL  = 'https://api.openweathermap.org/data/2.5/weather'
_FORECAST_URL = 'https://api.openweathermap.org/data/2.5/forecast'


def _weather_emoji(weather_id: int) -> str:
    if 200 <= weather_id < 300:
        return '⛈️'
    if 300 <= weather_id < 400:
        return '🌦️'
    if 500 <= weather_id < 600:
        return '🌧️'
    if 600 <= weather_id < 700:
        return '❄️'
    if 700 <= weather_id < 800:
        return '🌫️'
    if weather_id == 800:
        return '☀️'
    if 800 < weather_id < 900:
        return '🌤️'
    return '🌡️'


class WeatherModule(PebbleModule):
    name         = 'weather'
    display_name = 'Weather'
    description  = 'Get current weather and forecasts. Free API key at openweathermap.org/api'
    icon         = '🌤️'
    config_fields = [
        {'key': 'api_key',  'label': 'OpenWeatherMap API key',                      'type': 'password'},
        {'key': 'location', 'label': 'Default location (e.g. "Pittsburgh, US")',     'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(self.cfg.get('api_key', '').strip())

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'weather'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['current', 'forecast'],
                    'description': 'current weather or 24h forecast',
                },
                'location': {
                    'type': 'string',
                    'description': 'City name, overrides default (optional)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = 'current', location: str = '', **_) -> str:
        api_key = self.cfg.get('api_key', '').strip()
        if not api_key:
            return 'Weather not configured — add your OpenWeatherMap API key in Settings.'

        city = location.strip() or self.cfg.get('location', '').strip()
        if not city:
            return (
                'No location set — configure in Settings or provide a city name.'
            )

        try:
            if action == 'current':
                return self._current(api_key, city)
            elif action == 'forecast':
                return self._forecast(api_key, city)
            else:
                return f'Unknown action "{action}". Valid actions: current, forecast.'
        except Exception as e:
            return f'Weather error: {str(e)}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _current(self, api_key: str, city: str) -> str:
        try:
            resp = requests.get(
                _CURRENT_URL,
                params={'q': city, 'appid': api_key, 'units': 'imperial'},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            city_name   = data.get('name', city)
            country     = data.get('sys', {}).get('country', '')
            label       = f'{city_name}, {country}' if country else city_name

            weather_id  = data['weather'][0]['id']
            description = data['weather'][0]['description'].title()
            emoji       = _weather_emoji(weather_id)

            temp        = round(data['main']['temp'])
            feels_like  = round(data['main']['feels_like'])
            humidity    = data['main']['humidity']
            wind_speed  = round(data.get('wind', {}).get('speed', 0))

            return (
                f'{label} {emoji}\n'
                f'{temp}°F — {description}\n'
                f'Feels like {feels_like}°F | Humidity {humidity}% | Wind {wind_speed} mph'
            )
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return f"Location not found: '{city}'. Try a format like 'Pittsburgh, US'."
            return f'Weather error: {str(e)}'

    def _forecast(self, api_key: str, city: str) -> str:
        try:
            resp = requests.get(
                _FORECAST_URL,
                params={'q': city, 'appid': api_key, 'units': 'imperial', 'cnt': 8},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            city_name = data.get('city', {}).get('name', city)
            entries   = data.get('list', [])

            lines: list[str] = [f'24-hour forecast for {city_name}:']
            for i, entry in enumerate(entries):
                temp        = round(entry['main']['temp'])
                weather_id  = entry['weather'][0]['id']
                description = entry['weather'][0]['description'].title()
                emoji       = _weather_emoji(weather_id)

                dt_txt = entry.get('dt_txt', '')
                if i == 0:
                    time_label = 'Now '
                else:
                    try:
                        dt = datetime.datetime.strptime(dt_txt, '%Y-%m-%d %H:%M:%S')
                        hour = dt.hour
                        if hour == 0:
                            time_label = '12am'
                        elif hour < 12:
                            time_label = f'{hour}am '
                        elif hour == 12:
                            time_label = '12pm'
                        else:
                            time_label = f'{hour - 12}pm '
                    except ValueError:
                        time_label = dt_txt[-5:] if len(dt_txt) >= 5 else dt_txt

                lines.append(f'{time_label}  →  {temp}°F {description} {emoji}')

            return '\n'.join(lines)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return f"Location not found: '{city}'. Try a format like 'Pittsburgh, US'."
            return f'Weather error: {str(e)}'
