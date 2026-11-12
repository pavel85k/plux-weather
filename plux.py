from __future__ import annotations

"""Plux Weather — atmospheric desktop weather app for Windows.

Core dependencies are Python's standard library. Optional integrations:
    pip install pywebview pystray pillow winotify

Run: python plux.py
The app starts in the background. Double-tap Ctrl+Shift to show the UI.
"""

import ctypes
import http.server
import json
import logging
import math
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_NAME = "Plux Weather"
APP_VERSION = "4.1.0"
POLL_SECONDS = 300
STATE_REFRESH_SECONDS = 15
HOTKEY_WINDOW_SECONDS = 1.35
LANGUAGES = ("English", "Русский", "Українська", "Deutsch")

# ---------------------------------------------------------------------------
# Paths, logging and persistent data
# ---------------------------------------------------------------------------

def resource_path(relative: str) -> Path:
    """Resolve bundled PyInstaller resources or files beside this script."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def user_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PluxWeather"
    root.mkdir(parents=True, exist_ok=True)
    return root


DATA_DIR = user_data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.json"
LOG_FILE = DATA_DIR / "plux.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("plux")

DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "English",
    "notify_severe": True,
    "notify_change": True,
    "notify_weather": True,
    "season_music": True,
    "visual_time_minutes": None,
    "visual_season": "auto",
    "location": None,
    "last_temperature": None,
    "last_weather_code": None,
    "last_severe_code": None,
    "last_notification_at": None,
}


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default.copy()
    except (OSError, ValueError, TypeError):
        return default.copy()


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def fetch_json(url: str, timeout: int = 12) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"PluxWeather/{APP_VERSION}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(2_000_000)
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Unexpected API response")
    return parsed


# ---------------------------------------------------------------------------
# Languages and weather metadata
# ---------------------------------------------------------------------------
TEXT: dict[str, dict[str, str]] = {
    "English": {
        "app": "Plux Weather", "subtitle": "A little atmosphere outside your window",
        "feels": "Feels like", "humidity": "Humidity", "wind": "Wind",
        "precip": "Precipitation", "sunrise": "Sunrise", "sunset": "Sunset",
        "today": "Today", "hourly": "Next hours", "week": "7-day outlook",
        "temperature": "Temperature", "rain_chance": "Rain chance", "wind_tab": "Wind",
        "settings": "Settings", "language": "Language", "alerts": "Alerts",
        "severe_alert": "Severe weather alerts", "temp_alert": "Temperature changes (5 °C or more)",
        "weather_alert": "Weather condition changes", "music": "Seasonal ambience",
        "music_hint": "Background music · 40% volume", "close": "Close", "save": "Saved automatically",
        "refresh": "Refresh weather", "updated": "Updated", "loading": "Gathering the sky…",
        "offline": "Weather service is temporarily unavailable", "location": "Your location",
        "day": "Day", "sunset_phase": "Golden hour", "night": "Night",
        "winter": "Winter", "spring": "Spring", "summer": "Summer", "autumn": "Autumn",
        "clear": "Clear sky", "mostly_clear": "Mostly clear", "cloudy": "Cloudy",
        "fog": "Fog", "drizzle": "Drizzle", "rain": "Rain", "snow": "Snow",
        "showers": "Showers", "storm": "Thunderstorm", "unknown": "Weather",
        "shovel": "Shovel", "broom": "Broom", "tool_hint": "Choose a tool, then drag across the scene",
        "snow_hint": "Drag the shovel through the snow to clear a path.",
        "leaf_hint": "Sweep across the leaves to gather them away.",
        "air_note": "Plux reports weather alerts, not official civil-defence or air-raid alerts.",
        "no_data": "Waiting for the first weather update…", "feels_short": "Feels", "world_controls": "World controls", "world_time": "Time of day", "world_season": "Season", "default": "Default", "automatic": "Automatic",
    },
    "Русский": {
        "app": "Погода Plux", "subtitle": "Немного атмосферы за твоим окном",
        "feels": "Ощущается как", "humidity": "Влажность", "wind": "Ветер",
        "precip": "Осадки", "sunrise": "Восход", "sunset": "Закат",
        "today": "Сегодня", "hourly": "Ближайшие часы", "week": "Прогноз на 7 дней",
        "temperature": "Температура", "rain_chance": "Вероятность осадков", "wind_tab": "Ветер",
        "settings": "Настройки", "language": "Язык", "alerts": "Уведомления",
        "severe_alert": "Оповещать об опасной погоде и грозе", "temp_alert": "Изменение температуры на 5 °C и более",
        "weather_alert": "Изменение погодных условий", "music": "Сезонная атмосфера",
        "music_hint": "Фоновая музыка · громкость 40%", "close": "Закрыть", "save": "Сохраняется автоматически",
        "refresh": "Обновить погоду", "updated": "Обновлено", "loading": "Собираю погоду…",
        "offline": "Сервис погоды временно недоступен", "location": "Твоё местоположение",
        "day": "День", "sunset_phase": "Золотой час", "night": "Ночь",
        "winter": "Зима", "spring": "Весна", "summer": "Лето", "autumn": "Осень",
        "clear": "Ясно", "mostly_clear": "Преимущественно ясно", "cloudy": "Облачно",
        "fog": "Туман", "drizzle": "Морось", "rain": "Дождь", "snow": "Снег",
        "showers": "Ливень", "storm": "Гроза", "unknown": "Погода",
        "shovel": "Лопата", "broom": "Метла", "tool_hint": "Выбери инструмент и проведи им по сцене",
        "snow_hint": "Проводи лопатой по снегу, чтобы расчистить дорожку.",
        "leaf_hint": "Проводи метлой по листьям, чтобы смести их.",
        "air_note": "Plux сообщает о погодных угрозах, но не получает официальные сигналы воздушной тревоги.",
        "no_data": "Ожидаю первое обновление погоды…", "feels_short": "Ощущается", "world_controls": "Настройки мира", "world_time": "Время суток", "world_season": "Время года", "default": "По умолчанию", "automatic": "Автоматически",
    },
    "Українська": {
        "app": "Погода Plux", "subtitle": "Трохи атмосфери за твоїм вікном",
        "feels": "Відчувається як", "humidity": "Вологість", "wind": "Вітер",
        "precip": "Опади", "sunrise": "Схід", "sunset": "Захід",
        "today": "Сьогодні", "hourly": "Найближчі години", "week": "Прогноз на 7 днів",
        "temperature": "Температура", "rain_chance": "Імовірність опадів", "wind_tab": "Вітер",
        "settings": "Налаштування", "language": "Мова", "alerts": "Сповіщення",
        "severe_alert": "Сповіщати про небезпечну погоду та грозу", "temp_alert": "Зміна температури на 5 °C або більше",
        "weather_alert": "Зміна погодних умов", "music": "Сезонна атмосфера",
        "music_hint": "Фонова музика · гучність 40%", "close": "Закрити", "save": "Зберігається автоматично",
        "refresh": "Оновити погоду", "updated": "Оновлено", "loading": "Збираю погоду…",
        "offline": "Сервіс погоди тимчасово недоступний", "location": "Твоє місце",
        "day": "День", "sunset_phase": "Золота година", "night": "Ніч",
        "winter": "Зима", "spring": "Весна", "summer": "Літо", "autumn": "Осінь",
        "clear": "Ясно", "mostly_clear": "Переважно ясно", "cloudy": "Хмарно",
        "fog": "Туман", "drizzle": "Мряка", "rain": "Дощ", "snow": "Сніг",
        "showers": "Злива", "storm": "Гроза", "unknown": "Погода",
        "shovel": "Лопата", "broom": "Мітла", "tool_hint": "Обери інструмент і проведи ним по сцені",
        "snow_hint": "Проведи лопатою по снігу, щоб розчистити доріжку.",
        "leaf_hint": "Проведи мітлою по листю, щоб прибрати його.",
        "air_note": "Plux повідомляє про погодні загрози, але не отримує офіційні сигнали повітряної тривоги.",
        "no_data": "Очікую на перше оновлення погоди…", "feels_short": "Відчувається", "world_controls": "Налаштування світу", "world_time": "Час доби", "world_season": "Пора року", "default": "За замовчуванням", "automatic": "Автоматично",
    },
    "Deutsch": {
        "app": "Plux Wetter", "subtitle": "Ein bisschen Atmosphäre vor deinem Fenster",
        "feels": "Gefühlt", "humidity": "Luftfeuchtigkeit", "wind": "Wind",
        "precip": "Niederschlag", "sunrise": "Sonnenaufgang", "sunset": "Sonnenuntergang",
        "today": "Heute", "hourly": "Nächste Stunden", "week": "7-Tage-Ausblick",
        "temperature": "Temperatur", "rain_chance": "Regenwahrscheinlichkeit", "wind_tab": "Wind",
        "settings": "Einstellungen", "language": "Sprache", "alerts": "Benachrichtigungen",
        "severe_alert": "Vor Unwettern und Gewittern warnen", "temp_alert": "Temperaturänderung um mindestens 5 °C",
        "weather_alert": "Wetteränderungen", "music": "Saisonale Atmosphäre",
        "music_hint": "Hintergrundmusik · 40% Lautstärke", "close": "Schließen", "save": "Automatisch gespeichert",
        "refresh": "Wetter aktualisieren", "updated": "Aktualisiert", "loading": "Der Himmel wird gelesen…",
        "offline": "Wetterdienst vorübergehend nicht verfügbar", "location": "Dein Standort",
        "day": "Tag", "sunset_phase": "Goldene Stunde", "night": "Nacht",
        "winter": "Winter", "spring": "Frühling", "summer": "Sommer", "autumn": "Herbst",
        "clear": "Klarer Himmel", "mostly_clear": "Überwiegend klar", "cloudy": "Bewölkt",
        "fog": "Nebel", "drizzle": "Nieselregen", "rain": "Regen", "snow": "Schnee",
        "showers": "Schauer", "storm": "Gewitter", "unknown": "Wetter",
        "shovel": "Schaufel", "broom": "Besen", "tool_hint": "Werkzeug wählen und über die Szene ziehen",
        "snow_hint": "Mit der Schaufel durch den Schnee ziehen.",
        "leaf_hint": "Mit dem Besen über die Blätter fegen.",
        "air_note": "Plux meldet Wetterwarnungen, aber keine offiziellen Zivil- oder Luftalarmmeldungen.",
        "no_data": "Warte auf das erste Wetterupdate…", "feels_short": "Gefühlt", "world_controls": "Welteinstellungen", "world_time": "Tageszeit", "world_season": "Jahreszeit", "default": "Standard", "automatic": "Automatisch",
    },
}

WEATHER_META: dict[int, tuple[str, str]] = {
    0: ("clear", "☀️"), 1: ("mostly_clear", "🌤️"), 2: ("cloudy", "⛅"), 3: ("cloudy", "☁️"),
    45: ("fog", "🌫️"), 48: ("fog", "🌫️"), 51: ("drizzle", "🌦️"), 53: ("drizzle", "🌦️"),
    55: ("drizzle", "🌧️"), 56: ("drizzle", "🌧️"), 57: ("drizzle", "🌧️"), 61: ("rain", "🌧️"),
    63: ("rain", "🌧️"), 65: ("rain", "🌧️"), 66: ("rain", "🌧️"), 67: ("rain", "🌧️"),
    71: ("snow", "🌨️"), 73: ("snow", "❄️"), 75: ("snow", "❄️"), 77: ("snow", "🌨️"),
    80: ("showers", "🌦️"), 81: ("showers", "🌧️"), 82: ("showers", "🌧️"),
    85: ("snow", "🌨️"), 86: ("snow", "❄️"), 95: ("storm", "⛈️"), 96: ("storm", "⛈️"), 99: ("storm", "⛈️"),
}

def warning_icon_file(code: int) -> str:
    if code in SEVERE_CODES:
        return "торнадо.png"
    if code in (71, 73, 75, 77, 85, 86):
        return "снегопад.png"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "дождь.png"
    if code in (0, 1):
        return "солнечно.png"
    return "ветрено.png"


SEVERE_CODES = {95, 96, 99}


def weather_key(code: int) -> str:
    return WEATHER_META.get(code, ("unknown", "⛅"))[0]


def weather_emoji(code: int, is_day: bool = True) -> str:
    if code == 0 and not is_day:
        return "🌙"
    return WEATHER_META.get(code, ("unknown", "⛅"))[1]


def season_for_month(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


# ---------------------------------------------------------------------------
# Location, weather API and notifications
# ---------------------------------------------------------------------------

def discover_location() -> dict[str, Any]:
    """Approximate city-level location from IP. No GPS or exact address is used."""
    providers = (
        ("https://ipapi.co/json/", "latitude", "longitude", "city", "country_name", "timezone"),
        ("http://ip-api.com/json/", "lat", "lon", "city", "country", "timezone"),
    )
    for url, lat_key, lon_key, city_key, country_key, tz_key in providers:
        try:
            data = fetch_json(url, timeout=7)
            if data.get("status") == "fail":
                continue
            lat = data.get(lat_key)
            lon = data.get(lon_key)
            if lat is None or lon is None:
                continue
            return {
                "latitude": float(lat), "longitude": float(lon),
                "city": str(data.get(city_key) or ""),
                "country": str(data.get(country_key) or ""),
                "timezone": str(data.get(tz_key) or "UTC"),
                "source": "ip",
            }
        except Exception as exc:
            log.warning("Location provider failed: %s", exc)
    # Fallback is a city-level estimate, not a claim about the user's exact location.
    return {
        "latitude": 47.0105, "longitude": 28.8638, "city": "Chișinău",
        "country": "Moldova", "timezone": "Europe/Chisinau", "source": "fallback",
    }


def notify_windows(title: str, message: str, icon: str | None = None, sound: str | None = None) -> None:
    try:
        from winotify import Notification
        kwargs: dict[str, Any] = {"app_id": APP_NAME, "title": title, "msg": message}
        if icon:
            icon_path = resource_path(icon)
            if icon_path.is_file():
                kwargs["icon"] = str(icon_path.resolve())
        Notification(**kwargs).show()
    except Exception as exc:
        log.info("Toast notification unavailable: %s", exc)
    if sound:
        play_sound(sound)


def play_sound(relative_path: str) -> None:
    path = resource_path(relative_path)
    if not path.is_file():
        return
    # MCI ships with Windows and can play MP3 without an extra Python package.
    try:
        winmm = ctypes.windll.winmm
        alias = f"plux_{threading.get_ident()}_{int(time.time() * 1000)}"
        command = f'open "{path.resolve()}" type mpegvideo alias {alias}'
        if winmm.mciSendStringW(command, None, 0, None) == 0:
            winmm.mciSendStringW(f"setaudio {alias} volume to 400", None, 0, None)
            winmm.mciSendStringW(f"play {alias}", None, 0, None)
    except Exception as exc:
        log.debug("Sound playback failed: %s", exc)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------
class PluxApp:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)
        for key, default in DEFAULT_SETTINGS.items():
            self.settings.setdefault(key, default)
        if self.settings.get("language") not in LANGUAGES:
            self.settings["language"] = "English"
        self.history = load_json(HISTORY_FILE, {"readings": []})
        if not isinstance(self.history.get("readings"), list):
            self.history = {"readings": []}
        self.weather: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_success = 0.0
        self.refresh_requested = threading.Event()
        self.stop_event = threading.Event()
        self.server: ThreadedHTTPServer | None = None
        self.port = 0
        self.webview_window: Any = None
        self.tray_icon: Any = None
        self._save_settings()

    def _save_settings(self) -> None:
        save_json(SETTINGS_FILE, self.settings)

    def _text(self) -> dict[str, str]:
        return TEXT[self.settings.get("language", "English")]

    def _ensure_location(self) -> dict[str, Any]:
        location = self.settings.get("location")
        if isinstance(location, dict) and location.get("latitude") is not None and location.get("longitude") is not None:
            return location
        location = discover_location()
        self.settings["location"] = location
        self._save_settings()
        return location

    def update_weather(self, initial: bool = False) -> None:
        """Fetch current conditions and forecasts; state mutation is lock-protected."""
        try:
            with self.lock:
                location = self._ensure_location()
            query = urllib.parse.urlencode({
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
                "timezone": "auto", "forecast_days": 7, "forecast_hours": 24,
            })
            data = fetch_json(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=15)
            current = data["current"]
            daily = data.get("daily") or {}
            hourly = data.get("hourly") or {}
            temperature = float(current["temperature_2m"])
            code = int(current["weather_code"])
            temp_previous = self.settings.get("last_temperature")
            code_previous = self.settings.get("last_weather_code")
            severe_previous = self.settings.get("last_severe_code")
            lang = self.settings.get("language", "English")
            t = TEXT.get(lang, TEXT["English"])

            # Prepare all forecast values in Python; browser only displays these values.
            hourly_items: list[dict[str, Any]] = []
            h_times = hourly.get("time", [])
            h_temps = hourly.get("temperature_2m", [])
            h_rain = hourly.get("precipitation_probability", [])
            h_wind = hourly.get("wind_speed_10m", [])
            h_codes = hourly.get("weather_code", [])
            now_hour = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
            for idx, raw_time in enumerate(h_times[:24]):
                try:
                    point_time = datetime.fromisoformat(str(raw_time))
                    if point_time.hour < now_hour.hour and point_time.date() == now_hour.date():
                        continue
                    hourly_items.append({
                        "time": point_time.strftime("%H:%M"),
                        "temperature": round(float(h_temps[idx]), 1),
                        "rain": int(h_rain[idx] or 0),
                        "wind": round(float(h_wind[idx] or 0)),
                        "code": int(h_codes[idx] or 0),
                        "emoji": weather_emoji(int(h_codes[idx] or 0)),
                    })
                except (ValueError, TypeError, IndexError):
                    continue
                if len(hourly_items) >= 10:
                    break

            daily_items: list[dict[str, Any]] = []
            d_times = daily.get("time", [])
            for idx, raw_date in enumerate(d_times[:7]):
                try:
                    day_date = datetime.fromisoformat(str(raw_date))
                    d_code = int(daily.get("weather_code", [0])[idx] or 0)
                    daily_items.append({
                        "date": raw_date,
                        "day": day_date.strftime("%a"),
                        "day_short": day_date.strftime("%a").upper(),
                        "min": round(float(daily.get("temperature_2m_min", [0])[idx])),
                        "max": round(float(daily.get("temperature_2m_max", [0])[idx])),
                        "rain": int(daily.get("precipitation_probability_max", [0])[idx] or 0),
                        "code": d_code,
                        "emoji": weather_emoji(d_code),
                    })
                except (ValueError, TypeError, IndexError):
                    continue

            place = ", ".join(x for x in (location.get("city"), location.get("country")) if x)
            if not place:
                place = f"{location['latitude']:.2f}, {location['longitude']:.2f}"

            new_weather = {
                "place": place,
                "temperature": round(temperature, 1),
                "feels_like": round(float(current.get("apparent_temperature", temperature)), 1),
                "humidity": int(current.get("relative_humidity_2m", 0) or 0),
                "wind": round(float(current.get("wind_speed_10m", 0) or 0)),
                "wind_direction": int(current.get("wind_direction_10m", 0) or 0),
                "precipitation": round(float(current.get("precipitation", 0) or 0), 1),
                "code": code,
                "icon_file": warning_icon_file(code),
                "condition_key": weather_key(code),
                "emoji": weather_emoji(code, bool(current.get("is_day", 1))),
                "is_day": bool(current.get("is_day", 1)),
                "sunrise": str((daily.get("sunrise") or ["--:--"])[0])[-5:],
                "sunset": str((daily.get("sunset") or ["--:--"])[0])[-5:],
                "hourly": hourly_items,
                "daily": daily_items,
                "updated": datetime.now().astimezone().strftime("%H:%M"),
            }

            # Store history and state before notifications so a crash cannot repeat them endlessly.
            with self.lock:
                self.weather = new_weather
                self.last_error = None
                self.last_success = time.time()
                readings = self.history.get("readings", [])
                readings.append({"timestamp": datetime.now().astimezone().isoformat(timespec="minutes"), "temperature": temperature, "code": code})
                self.history = {"readings": readings[-1500:]}
                save_json(HISTORY_FILE, self.history)
                self.settings["last_temperature"] = temperature
                self.settings["last_weather_code"] = code
                self.settings["last_severe_code"] = code if code in SEVERE_CODES else None
                self._save_settings()

            # Notifications: only alert on a >=5°C step, condition transitions and entry into thunderstorm.
            if isinstance(temp_previous, (int, float)) and abs(temperature - float(temp_previous)) >= 5 and self.settings.get("notify_change", True):
                delta = abs(temperature - float(temp_previous))
                notify_windows(t["app"], f"{t['temperature']}: {temperature:.1f} °C (±{delta:.1f} °C)", "warning/дождь.png", "уведомление.mp3")
            if code_previous is not None and int(code_previous) != code and self.settings.get("notify_weather", True):
                key = weather_key(code)
                notify_windows(t["app"], t.get(key, t["unknown"]), "warning/снегопад.png" if key == "snow" else "warning/дождь.png", "уведомление.mp3")
            if code in SEVERE_CODES and (severe_previous not in SEVERE_CODES) and self.settings.get("notify_severe", True):
                notify_windows(t["app"], t["storm"], "warning/торнадо.png", "тривогауведомление.mp3")

        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)
            log.exception("Weather refresh failed")

    def state(self) -> dict[str, Any]:
        with self.lock:
            lang = self.settings.get("language", "English")
            t = TEXT.get(lang, TEXT["English"])
            real_now = datetime.now().astimezone()  # Always based on Windows system date/time.
            visual_minutes = self.settings.get("visual_time_minutes")
            now = real_now
            if isinstance(visual_minutes, int) and not isinstance(visual_minutes, bool):
                visual_minutes = max(0, min(1439, visual_minutes))
                now = real_now.replace(hour=visual_minutes // 60, minute=visual_minutes % 60,
                                       second=0, microsecond=0)
            else:
                visual_minutes = None

            visual_season = self.settings.get("visual_season", "auto")
            if visual_season not in ("auto", "winter", "spring", "summer", "autumn"):
                visual_season = "auto"
            season = visual_season if visual_season != "auto" else season_for_month(real_now.month)

            phase = "day"
            if self.weather:
                try:
                    sunrise = datetime.strptime(self.weather["sunrise"], "%H:%M").time()
                    sunset = datetime.strptime(self.weather["sunset"], "%H:%M").time()
                    current_time = now.time()
                    sunset_dt = now.replace(hour=sunset.hour, minute=sunset.minute, second=0, microsecond=0)
                    if current_time < sunrise or current_time > sunset:
                        phase = "night"
                    elif abs((now - sunset_dt).total_seconds()) <= 75 * 60:
                        phase = "sunset"
                except (ValueError, TypeError):
                    phase = "day" if 7 <= now.hour < 20 else "night"
            else:
                phase = "day" if 7 <= now.hour < 20 else "night"

            weather = dict(self.weather) if self.weather else None
            if weather:
                weather["condition"] = t.get(weather["condition_key"], t["unknown"])
            return {
                "version": APP_VERSION,
                "text": t,
                "settings": {
                    "language": lang,
                    "notify_severe": bool(self.settings.get("notify_severe", True)),
                    "notify_change": bool(self.settings.get("notify_change", True)),
                    "notify_weather": bool(self.settings.get("notify_weather", True)),
                    "visual_time_minutes": visual_minutes,
                    "visual_season": visual_season,
                },
                "weather": weather,
                "error": self.last_error,
                "clock": now.strftime("%H:%M"),
                "date": real_now.strftime("%A, %d %B"),
                "season": season,
                "phase": phase,
                "updated_age": max(0, int(time.time() - self.last_success)) if self.last_success else None,
            }

    def update_settings(self, values: dict[str, Any]) -> None:
        allowed = {"language", "notify_severe", "notify_change", "notify_weather",
                   "visual_time_minutes", "visual_season"}
        with self.lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                if key == "language":
                    if value in LANGUAGES:
                        self.settings[key] = value
                elif key == "visual_time_minutes":
                    if value is None or value == "":
                        self.settings[key] = None
                    else:
                        try:
                            self.settings[key] = max(0, min(1439, int(value)))
                        except (TypeError, ValueError):
                            continue
                elif key == "visual_season":
                    if value in ("auto", "winter", "spring", "summer", "autumn"):
                        self.settings[key] = value
                else:
                    self.settings[key] = bool(value)
            self._save_settings()

    def request_refresh(self) -> None:
        self.refresh_requested.set()

    def poll_loop(self) -> None:
        while not self.stop_event.is_set():
            self.update_weather()
            self.refresh_requested.wait(POLL_SECONDS)
            self.refresh_requested.clear()


# ---------------------------------------------------------------------------
# Atmospheric web UI — all weather/time values arrive from Python
# ---------------------------------------------------------------------------
HTML_PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#111b2b"><link id="pageFavicon" rel="icon" type="image/x-icon" href="/favicon.ico"><title>Plux Weather</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{--ink:#f5f7fb;--muted:#b8c6d8;--soft:rgba(229,239,255,.72);--glass:rgba(15,28,48,.44);--glass2:rgba(255,255,255,.075);--line:rgba(255,255,255,.14);--gold:#ffd98b;--blue:#9bd8ff;--shadow:0 25px 80px rgba(0,0,0,.27)}
*{box-sizing:border-box}html,body{margin:0;width:100%;min-height:100%;font-family:'DM Sans','Segoe UI',sans-serif;color:var(--ink)}
body{min-height:100vh;overflow-x:hidden;background:transparent;transition:background 1.5s ease}
body.day{background:linear-gradient(145deg,rgba(121,184,209,.68) 0%,rgba(77,135,170,.64) 37%,rgba(35,62,91,.72) 100%)}
body.sunset{background:linear-gradient(150deg,rgba(182,125,117,.70) 0%,rgba(118,93,126,.69) 42%,rgba(40,60,91,.76) 100%)}
body.night{background:linear-gradient(150deg,rgba(17,27,54,.78) 0%,rgba(26,41,72,.76) 48%,rgba(10,20,39,.84) 100%)}
body.winter.day{background:linear-gradient(145deg,rgba(158,201,223,.70) 0%,rgba(100,141,170,.68) 44%,rgba(49,75,105,.78) 100%)}
body.winter.night{background:linear-gradient(145deg,rgba(23,39,66,.80) 0%,rgba(34,60,91,.78) 48%,rgba(11,25,45,.86) 100%)}
body.autumn.day{background:linear-gradient(145deg,rgba(197,142,114,.73) 0%,rgba(157,118,95,.70) 44%,rgba(76,83,99,.78) 100%)}
#sky{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.atmosphere{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
.glow{position:absolute;width:46vw;height:46vw;min-width:360px;min-height:360px;right:-12vw;top:-23vw;border-radius:50%;background:radial-gradient(circle,rgba(255,226,169,.23),rgba(255,226,169,0) 68%);filter:blur(4px);transition:all 2s}
body.night .glow{background:radial-gradient(circle,rgba(136,171,255,.15),transparent 68%)}
.horizon{position:absolute;left:-8%;right:-8%;bottom:-170px;height:330px;border-radius:50% 50% 0 0;background:linear-gradient(180deg,rgba(29,62,75,.34),rgba(11,28,42,.76));filter:blur(3px)}
.horizon:before{content:'';position:absolute;left:10%;right:15%;top:-52px;height:160px;border-radius:50%;background:rgba(62,94,96,.27);filter:blur(22px)}
.app{position:relative;z-index:2;width:min(1160px,calc(100% - 42px));margin:0 auto;padding:25px 0 36px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:15px;margin:0 4px 18px}
.brand{display:flex;align-items:center;gap:12px}.brandmark{width:42px;height:42px;border-radius:15px;display:grid;place-items:center;background:linear-gradient(145deg,rgba(255,255,255,.27),rgba(255,255,255,.07));border:1px solid rgba(255,255,255,.24);box-shadow:0 9px 24px rgba(0,0,0,.13);font-size:23px;backdrop-filter:blur(12px)}
.brandname{font:800 17px Manrope,sans-serif;letter-spacing:.2px}.brandline{font-size:11px;color:var(--soft);margin-top:2px}
.top-actions{display:flex;gap:9px}.icon-btn{width:40px;height:40px;border-radius:14px;border:1px solid var(--line);background:rgba(255,255,255,.08);color:white;display:grid;place-items:center;cursor:pointer;backdrop-filter:blur(14px);transition:.2s;font-size:17px}.icon-btn:hover,.tool-btn:hover{background:rgba(255,255,255,.16);transform:translateY(-2px)}
.glass{background:linear-gradient(145deg,rgba(255,255,255,.14),rgba(255,255,255,.045));border:1px solid rgba(255,255,255,.2);box-shadow:var(--shadow),inset 0 1px 0 rgba(255,255,255,.14);backdrop-filter:blur(24px) saturate(135%);-webkit-backdrop-filter:blur(24px) saturate(135%)}
.hero{border-radius:30px;padding:28px 32px 24px;position:relative;overflow:hidden;min-height:280px}
.hero:before{content:'';position:absolute;width:420px;height:420px;right:-70px;top:-220px;border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,.18),transparent 67%);pointer-events:none}
.hero-grid{display:grid;grid-template-columns:1.1fr .9fr;gap:20px;align-items:center;position:relative;z-index:1}
.place-line{display:flex;align-items:center;gap:8px;color:var(--soft);font-size:13px}.pin{font-size:14px;opacity:.9}.current-row{display:flex;align-items:center;gap:19px;margin-top:10px}.weather-symbol{font-size:88px;line-height:1;filter:drop-shadow(0 12px 15px rgba(0,0,0,.17));animation:float 5s ease-in-out infinite;user-select:none}.weather-symbol img{width:94px;height:94px;object-fit:contain;filter:drop-shadow(0 12px 15px rgba(0,0,0,.17))}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
.temp{font:500 clamp(70px,8vw,100px)/.95 Manrope,sans-serif;letter-spacing:-7px;text-shadow:0 8px 30px rgba(0,0,0,.1)}.temp sup{font-size:25px;letter-spacing:-1px;vertical-align:top;margin:12px 0 0 8px;display:inline-block;font-weight:500}
.condition{font:700 19px Manrope,sans-serif;margin-top:10px}.feels{font-size:12px;color:var(--soft);margin-top:5px}
.clockbox{justify-self:end;text-align:right;position:relative;padding-right:7px}.clock{font:600 36px Manrope,sans-serif;letter-spacing:-1px}.date{font-size:13px;color:var(--soft);margin-top:4px}.phase{display:inline-flex;align-items:center;gap:7px;margin-top:14px;padding:7px 11px;border-radius:30px;background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.12);font-size:11px;color:#f3f5fa}.phase-dot{width:6px;height:6px;border-radius:50%;background:#ffe0a0;box-shadow:0 0 12px #ffe0a0}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:25px;position:relative;z-index:1}.metric{padding:12px 14px;border-radius:17px;background:rgba(7,20,37,.14);border:1px solid rgba(255,255,255,.095)}.metric-label{font-size:11px;color:var(--soft);display:flex;align-items:center;gap:6px}.metric-value{font:700 16px Manrope,sans-serif;margin-top:6px}.metric-value small{font:500 11px 'DM Sans';color:var(--soft)}
.section{margin-top:18px;border-radius:24px;padding:20px 22px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.section-title{font:700 15px Manrope,sans-serif}.section-kicker{font-size:11px;color:var(--soft);margin-top:3px}.tabs{display:flex;align-items:center;gap:4px;padding:4px;border-radius:13px;background:rgba(4,14,29,.18);border:1px solid rgba(255,255,255,.09)}.tab{border:0;border-radius:10px;background:transparent;color:rgba(255,255,255,.68);padding:8px 12px;font:600 11px 'DM Sans';cursor:pointer;transition:.2s}.tab.active{background:rgba(255,255,255,.17);color:#fff;box-shadow:0 3px 10px rgba(0,0,0,.12)}
.chart-wrap{height:142px;position:relative}.chart-wrap canvas{width:100%;height:100%;display:block}.chart-labels{display:flex;justify-content:space-between;margin-top:1px;color:rgba(255,255,255,.62);font-size:10px;padding:0 2px}
.hourly{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px}.hour{border-radius:16px;background:rgba(255,255,255,.055);border:1px solid rgba(255,255,255,.07);padding:12px 7px;text-align:center;min-width:0}.hour:first-child{background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.16)}.hour-time{font-size:10px;color:var(--soft)}.hour-icon{font-size:22px;margin:11px 0 9px}.hour-temp{font:700 13px Manrope}.hour-rain{font-size:10px;color:#b9e6ff;margin-top:5px;min-height:12px}
.week{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px}.day-card{border-radius:15px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.07);text-align:center;padding:12px 5px}.day-card:first-child{background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.16)}.day-name{font-size:10px;color:var(--soft);font-weight:700}.day-icon{font-size:23px;margin:10px 0}.day-temps{font:600 12px Manrope}.day-temps span{color:rgba(255,255,255,.58);font-weight:500;margin-left:4px}.day-rain{font-size:9px;color:#b9e6ff;margin-top:5px;min-height:11px}
.scene{position:fixed;inset:0;z-index:1;pointer-events:none;overflow:hidden}.tree{position:absolute;bottom:0;width:min(19vw,210px);height:min(32vh,270px);opacity:0;transform:translateY(30px);transition:opacity 1.3s,transform 1.3s;filter:drop-shadow(0 14px 12px rgba(5,13,20,.2))}.tree.left{left:1.5%}.tree.right{right:1.5%;transform:translateY(30px) scaleX(-1)}body.autumn .tree,body.winter .tree{opacity:.78;transform:translateY(0)}body.autumn .tree.right,body.winter .tree.right{transform:translateY(0) scaleX(-1)}.tree svg{width:100%;height:100%;overflow:visible}.tree-trunk{fill:#544039}.tree-crown{fill:#355f57}.autumn .tree-crown{fill:#bd7042}.winter .tree-crown{fill:#527b83}
#weatherCanvas{position:fixed;inset:0;z-index:3;pointer-events:none;touch-action:none}.tool-btn{border:1px solid rgba(255,255,255,.13);border-radius:12px;background:rgba(255,255,255,.07);color:#fff;padding:9px 12px;font:600 11px 'DM Sans';cursor:pointer;transition:.2s}
.footer{display:flex;justify-content:space-between;align-items:center;padding:15px 5px 0;color:rgba(255,255,255,.62);font-size:10px}.status{display:flex;align-items:center;gap:7px}.status-dot{width:6px;height:6px;border-radius:50%;background:#9ce4c0;box-shadow:0 0 10px #9ce4c0}.status-dot.offline{background:#ffc28b;box-shadow:0 0 10px #ffc28b}
.overlay{position:fixed;inset:0;z-index:20;display:none;place-items:center;padding:20px;background:rgba(4,10,20,.35);backdrop-filter:blur(10px)}.overlay.open{display:grid;animation:fade .2s}@keyframes fade{from{opacity:0}to{opacity:1}}.settings-card{width:min(490px,100%);border-radius:25px;padding:23px;background:linear-gradient(145deg,rgba(28,48,72,.9),rgba(13,25,43,.91));border:1px solid rgba(255,255,255,.2);box-shadow:0 30px 90px rgba(0,0,0,.4)}.settings-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}.settings-head h2{font:800 19px Manrope;margin:0}.settings-group{margin-top:16px}.settings-label{font:700 11px Manrope;color:rgba(255,255,255,.64);text-transform:uppercase;letter-spacing:1.3px;margin-bottom:8px}.setting-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 0;border-bottom:1px solid rgba(255,255,255,.09);font-size:12px}.setting-row:last-child{border-bottom:0}.setting-row small{display:block;color:rgba(255,255,255,.52);font-size:10px;margin-top:4px;line-height:1.4}.switch{appearance:none;width:38px;height:22px;border-radius:20px;background:rgba(255,255,255,.2);position:relative;cursor:pointer;flex:none;transition:.2s}.switch:before{content:'';position:absolute;left:3px;top:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:.2s}.switch:checked{background:#78bce0}.switch:checked:before{transform:translateX(16px)}select{color:#fff;background:#263c58;border:1px solid rgba(255,255,255,.2);border-radius:10px;padding:8px 10px;outline:none;font:600 11px 'DM Sans'}select option{background:#20334d;color:#fff}.note{font-size:10px;color:rgba(255,255,255,.48);line-height:1.5;margin-top:15px}.error{position:fixed;top:15px;left:50%;transform:translateX(-50%);z-index:30;background:rgba(93,37,41,.85);border:1px solid rgba(255,190,190,.24);padding:10px 15px;border-radius:12px;color:#ffe0e0;font-size:11px;display:none;backdrop-filter:blur(12px)}

#snowSvg{position:fixed;inset:0;width:100%;height:100%;z-index:4;pointer-events:none;overflow:visible;opacity:0;transition:opacity 1.2s}
body.winter #snowSvg{opacity:1}
.world-tool{position:fixed;left:0;top:0;width:76px;height:116px;z-index:7;display:none;align-items:center;justify-content:center;cursor:grab;touch-action:none;user-select:none;filter:drop-shadow(0 9px 8px rgba(0,0,0,.28));transform-origin:50% 75%;will-change:transform;pointer-events:auto}
.world-tool.visible{display:flex}.world-tool.dragging{cursor:grabbing;filter:drop-shadow(0 13px 14px rgba(0,0,0,.35))}
.world-tool svg{width:100%;height:100%;overflow:visible;pointer-events:none}
.world-tool.broom{width:126px;height:76px}
.world-tool:after{content:'';position:absolute;inset:-12px;border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,.08),transparent 70%);z-index:-1}
.world-card{max-width:430px}.world-time-row{display:flex;align-items:center;gap:14px}.world-time-row input{flex:1;accent-color:#b9ddf7}.world-time-row output{min-width:48px;text-align:right;font:700 15px Manrope;color:#fff;font-variant-numeric:tabular-nums}.world-actions{display:flex;justify-content:flex-end;margin-top:20px}.world-actions .tool-btn{padding:10px 17px}
.tree svg{transform-origin:50% 100%;animation:treeSway 8s ease-in-out infinite}.tree.right svg{animation-delay:-3.5s}@keyframes treeSway{0%,100%{transform:rotate(-.7deg)}50%{transform:rotate(.8deg)}}

@media(max-width:760px){.app{width:calc(100% - 22px);padding-top:13px}.hero{padding:22px 19px}.hero-grid{grid-template-columns:1fr auto}.weather-symbol{font-size:64px}.temp{font-size:68px;letter-spacing:-5px}.clock{font-size:27px}.metrics{grid-template-columns:repeat(2,1fr);margin-top:19px}.section{padding:16px 13px}.hourly{grid-template-columns:repeat(4,minmax(0,1fr))}.hour:nth-child(n+5){display:none}.week{grid-template-columns:repeat(4,minmax(0,1fr))}.day-card:nth-child(n+5){display:none}.tree{width:24vw;opacity:.42!important}}
@media(max-width:430px){.brandline{display:none}.hero-grid{grid-template-columns:1fr}.clockbox{position:absolute;right:0;top:0}.clock{font-size:23px}.date{font-size:10px}.current-row{margin-top:28px}.metrics{gap:6px}.metric{padding:10px}.section-head{align-items:flex-start;flex-direction:column}.tabs{width:100%;justify-content:space-between}.tab{flex:1;padding:8px 5px}.footer{font-size:9px}}
</style>
</head>
<body class="day spring">
<canvas id="sky"></canvas><div class="atmosphere"><div class="glow"></div><div class="horizon"></div></div>
<div class="scene" id="scene">
 <div class="tree left"><svg viewBox="0 0 180 260" aria-hidden="true"><path class="tree-trunk" d="M78 260 Q91 210 84 158 Q65 135 48 121 L56 113 Q79 128 87 140 Q89 104 69 78 L78 71 Q97 94 98 124 Q116 100 132 91 L138 99 Q111 119 101 145 Q112 169 110 193 Q131 169 148 168 L153 177 Q128 192 112 214 L118 260Z"/><g class="tree-crown"><circle cx="72" cy="75" r="32"/><circle cx="106" cy="66" r="35"/><circle cx="132" cy="100" r="29"/><circle cx="63" cy="113" r="30"/><circle cx="101" cy="119" r="34"/></g></svg></div>
 <div class="tree right"><svg viewBox="0 0 180 260" aria-hidden="true"><path class="tree-trunk" d="M78 260 Q91 210 84 158 Q65 135 48 121 L56 113 Q79 128 87 140 Q89 104 69 78 L78 71 Q97 94 98 124 Q116 100 132 91 L138 99 Q111 119 101 145 Q112 169 110 193 Q131 169 148 168 L153 177 Q128 192 112 214 L118 260Z"/><g class="tree-crown"><circle cx="72" cy="75" r="32"/><circle cx="106" cy="66" r="35"/><circle cx="132" cy="100" r="29"/><circle cx="63" cy="113" r="30"/><circle cx="101" cy="119" r="34"/></g></svg></div>
</div>
<canvas id="weatherCanvas"></canvas>
<main class="app">
 <header class="topbar"><div class="brand"><div class="brandmark">✦</div><div><div class="brandname" id="brandTitle">Plux Weather</div><div class="brandline" id="subtitle">A little atmosphere outside your window</div></div></div>
 <div class="top-actions"><button class="icon-btn" id="refreshBtn" title="Refresh weather">↻</button><button class="icon-btn" id="settingsBtn" title="Settings">⚙</button></div></header>
 <section class="hero glass">
  <div class="hero-grid"><div><div class="place-line"><span class="pin">⌖</span><span id="place">Finding your location…</span></div>
   <div class="current-row"><div class="weather-symbol" id="weatherEmoji">🌤️</div><div><div class="temp" id="temperature">--<sup>°C</sup></div><div class="condition" id="condition">Gathering the sky…</div><div class="feels" id="feels">Feels like --°</div></div></div>
  </div><div class="clockbox"><div class="clock" id="clock">--:--</div><div class="date" id="date">--</div><div class="phase"><span class="phase-dot"></span><span id="phaseText">Day</span></div></div></div>
  <div class="metrics"><div class="metric"><div class="metric-label">◌ <span id="humidityLabel">Humidity</span></div><div class="metric-value" id="humidity">--<small>%</small></div></div><div class="metric"><div class="metric-label">〰 <span id="windLabel">Wind</span></div><div class="metric-value" id="wind">--<small> km/h</small></div></div><div class="metric"><div class="metric-label">⌁ <span id="precipLabel">Precipitation</span></div><div class="metric-value" id="precip">--<small> mm</small></div></div><div class="metric"><div class="metric-label">☼ <span id="sunriseLabel">Sunrise</span></div><div class="metric-value" id="sunrise">--:--</div></div></div>
 </section>
 <section class="section glass"><div class="section-head"><div><div class="section-title" id="chartTitle">Temperature</div><div class="section-kicker" id="chartKicker">A little look ahead</div></div><div class="tabs"><button class="tab active" data-kind="temperature">Temperature</button><button class="tab" data-kind="rain">Rain chance</button><button class="tab" data-kind="wind">Wind</button></div></div><div class="chart-wrap"><canvas id="chart"></canvas></div><div class="chart-labels" id="chartLabels"></div></section>
 <section class="section glass"><div class="section-head"><div><div class="section-title" id="hourTitle">Next hours</div><div class="section-kicker" id="hourSub">The weather as it moves through the day</div></div></div><div class="hourly" id="hourly"></div></section>
 <section class="section glass"><div class="section-head"><div><div class="section-title" id="weekTitle">7-day outlook</div><div class="section-kicker" id="weekSub">A small preview of what's coming</div></div><div class="section-kicker" id="sunsetLine">Sunset --:--</div></div><div class="week" id="week"></div></section>
 <footer class="footer"><div class="status"><span class="status-dot" id="statusDot"></span><span id="statusText">Weather service connected</span></div><div id="updated">Updated --</div></footer>
</main>
<div id="seasonTool" class="world-tool" aria-label="Seasonal object" title="Drag me"></div>
<svg id="snowSvg" aria-hidden="true" viewBox="0 0 1000 700" preserveAspectRatio="none"><defs><linearGradient id="snowGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".96"/><stop offset=".34" stop-color="#e9f6ff" stop-opacity=".94"/><stop offset="1" stop-color="#b8d3e5" stop-opacity=".98"/></linearGradient><filter id="snowGlow" x="-20%" y="-30%" width="140%" height="160%"><feGaussianBlur stdDeviation="7"/></filter></defs><path id="snowShadow" fill="#d7eaff" opacity=".18" filter="url(#snowGlow)"></path><path id="snowShape" fill="url(#snowGradient)" stroke="rgba(255,255,255,.88)" stroke-width="1.5"></path></svg>
<div class="overlay" id="settingsOverlay"><div class="settings-card"><div class="settings-head"><h2 id="settingsTitle">Settings</h2><button class="icon-btn" id="closeSettings">×</button></div>
 <div class="settings-group"><div class="settings-label" id="languageLabel">Language</div><div class="setting-row"><span id="languageRow">Interface language</span><select id="languageSelect"><option>English</option><option>Русский</option><option>Українська</option><option>Deutsch</option></select></div></div>
 <div class="settings-group"><div class="settings-label" id="alertsLabel">Notifications</div>
  <label class="setting-row"><span id="severeLabel">Severe weather alerts</span><input class="switch" type="checkbox" id="notifySevere"></label>
  <label class="setting-row"><span id="tempLabel">Temperature changes (5 °C or more)</span><input class="switch" type="checkbox" id="notifyChange"></label>
  <label class="setting-row"><span id="weatherLabel">Weather condition changes</span><input class="switch" type="checkbox" id="notifyWeather"></label>
 </div>
 <div class="note" id="airNote">Plux reports weather alerts, not official civil-defence or air-raid alerts.</div><div class="note" id="savedText">Saved automatically</div>
</div></div>
<div class="overlay" id="worldOverlay"><div class="settings-card world-card"><div class="settings-head"><h2 id="worldTitle">World controls</h2><button class="icon-btn" id="closeWorld">×</button></div>
 <div class="settings-group"><div class="settings-label" id="worldTimeLabel">Time of day</div><div class="world-time-row"><input type="range" id="worldTime" min="0" max="1439" step="1" value="720"><output id="worldTimeValue">12:00</output></div></div>
 <div class="settings-group"><div class="settings-label" id="worldSeasonLabel">Season</div><select id="worldSeason"><option value="auto">Automatic</option><option value="winter">Winter</option><option value="spring">Spring</option><option value="summer">Summer</option><option value="autumn">Autumn</option></select></div>
 <div class="world-actions"><button class="tool-btn" id="worldDefault">Default</button></div>
</div></div>
<div class="error" id="errorBox"></div>
<audio id="seasonAudio" autoplay loop preload="auto"></audio>
<script>
'use strict';
const STATE_REFRESH_SECONDS = 15;
const $ = id => document.getElementById(id);
let appState = null, chartKind = 'temperature', currentSeason = 'spring', musicSeason = null, decorSeason = null, activeFaviconSeason = null;
const tr = key => (appState && appState.text && appState.text[key]) || key;
const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, options={}) { const response = await fetch(path, {cache:'no-store', ...options}); if(!response.ok) throw new Error('HTTP '+response.status); return response.json().catch(()=>({ok:true})); }
function setText(id, value){ $(id).textContent = value; }
function applyLanguage(t){
 const seasonOptions=$('worldSeason').options; if(seasonOptions.length>=5){seasonOptions[0].text=t.automatic||'Automatic';seasonOptions[1].text=t.winter||'Winter';seasonOptions[2].text=t.spring||'Spring';seasonOptions[3].text=t.summer||'Summer';seasonOptions[4].text=t.autumn||'Autumn';}
 setText('brandTitle',t.app); setText('subtitle',t.subtitle); setText('humidityLabel',t.humidity); setText('windLabel',t.wind); setText('precipLabel',t.precip); setText('sunriseLabel',t.sunrise);
 setText('chartTitle',t.temperature); setText('hourTitle',t.hourly); setText('weekTitle',t.week); setText('settingsTitle',t.settings); setText('languageLabel',t.language); setText('alertsLabel',t.alerts); setText('severeLabel',t.severe_alert); setText('tempLabel',t.temp_alert); setText('weatherLabel',t.weather_alert); setText('closeSettings', '×'); setText('savedText',t.save); setText('airNote',t.air_note); setText('worldTitle',t.world_controls); setText('worldTimeLabel',t.world_time); setText('worldSeasonLabel',t.world_season); setText('worldDefault',t.default);
 document.querySelectorAll('.tab').forEach(b=>{const k=b.dataset.kind; b.textContent=k==='temperature'?t.temperature:k==='rain'?t.rain_chance:t.wind_tab;});
}
function render(state){
 appState=state; const t=state.text||{}; applyLanguage(t);
 document.body.className = `${state.phase||'day'} ${state.season||'spring'}`;
 currentSeason=state.season||'spring';
 setText('clock',state.clock||'--:--'); setText('date',state.date||''); setText('phaseText',t[state.phase]||t.day||'Day');
 const s=state.settings||{}; $('languageSelect').value=s.language||'English'; $('notifySevere').checked=!!s.notify_severe; $('notifyChange').checked=!!s.notify_change; $('notifyWeather').checked=!!s.notify_weather; $('worldSeason').value=s.visual_season||'auto'; const minute=Number.isInteger(s.visual_time_minutes)?s.visual_time_minutes:(Number((state.clock||'12:00').slice(0,2))*60+Number((state.clock||'12:00').slice(3,5))); $('worldTime').value=minute; updateWorldTimeLabel(minute);
 const w=state.weather;
 if(w){
  setText('place',w.place); $('temperature').innerHTML=`${Math.round(Number(w.temperature))}<sup>°C</sup>`; setText('condition',w.condition||''); setText('feels',`${t.feels||'Feels like'} ${Number(w.feels_like).toFixed(0)}°`); $('weatherEmoji').innerHTML=w.icon_file?`<img src="/assets/warning/${encodeURIComponent(w.icon_file)}" alt="" onerror="this.replaceWith(document.createTextNode('${safe(w.emoji||'⛅')}'))">`:safe(w.emoji||'⛅'); setText('humidity',`${w.humidity}%`); setText('wind',`${w.wind} km/h`); setText('precip',`${Number(w.precipitation).toFixed(1)} mm`); setText('sunrise',w.sunrise||'--:--'); setText('sunsetLine',`${t.sunset||'Sunset'} ${w.sunset||'--:--'}`); setText('updated',`${t.updated||'Updated'} ${w.updated||'--:--'}`);
  renderHourly(w.hourly||[]); renderWeek(w.daily||[]); drawChart();
 } else {setText('place',t.location||'');setText('condition',t.no_data||'');}
 const offline=!!state.error; $('statusDot').classList.toggle('offline',offline); setText('statusText',offline?(t.offline||'Offline'):'Weather service connected');
 if(state.error){$('errorBox').textContent=t.offline||state.error;$('errorBox').style.display='block';}else{$('errorBox').style.display='none';}
 updateSeasonDecor(); syncMusic(); updatePageFavicon(currentSeason);
}
function renderHourly(items){
 const host=$('hourly'); host.innerHTML=''; items.slice(0,8).forEach((item,i)=>{const el=document.createElement('div');el.className='hour';el.innerHTML=`<div class="hour-time">${safe(i===0?tr('today'):item.time)}</div><div class="hour-icon">${safe(item.emoji)}</div><div class="hour-temp">${Math.round(item.temperature)}°</div><div class="hour-rain">${item.rain?`💧 ${item.rain}%`:''}</div>`;host.appendChild(el);});
}
function renderWeek(items){
 const host=$('week');host.innerHTML='';items.slice(0,7).forEach((item,i)=>{const name=i===0?tr('today'):safe(item.day_short);const el=document.createElement('div');el.className='day-card';el.innerHTML=`<div class="day-name">${name}</div><div class="day-icon">${safe(item.emoji)}</div><div class="day-temps">${item.max}° <span>${item.min}°</span></div><div class="day-rain">${item.rain?`💧 ${item.rain}%`:''}</div>`;host.appendChild(el);});
}
function chartValues(){const items=appState?.weather?.hourly||[];return items.slice(0,8).map(x=>({label:x.time,value:chartKind==='temperature'?x.temperature:chartKind==='rain'?x.rain:x.wind}));}
function drawChart(){
 const canvas=$('chart'),rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);if(!rect.width||!rect.height)return;canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);const c=canvas.getContext('2d');c.scale(dpr,dpr);const W=rect.width,H=rect.height;const pts=chartValues();if(!pts.length)return;
 const vals=pts.map(p=>p.value),min=Math.min(...vals,chartKind==='temperature'?0:0),max=Math.max(...vals,chartKind==='temperature'?20:10),range=Math.max(1,max-min);const x0=12,x1=W-12,y0=H-20,y1=13;
 c.strokeStyle='rgba(255,255,255,.10)';c.lineWidth=1;for(let i=0;i<4;i++){let y=y1+(y0-y1)*i/3;c.beginPath();c.moveTo(x0,y);c.lineTo(x1,y);c.stroke();}
 const coords=pts.map((p,i)=>({x:x0+(x1-x0)*(pts.length===1?0:i/(pts.length-1)),y:y0-(p.value-min)/range*(y0-y1-8),p}));
 const grad=c.createLinearGradient(0,y1,0,H);grad.addColorStop(0,'rgba(255,222,155,.34)');grad.addColorStop(1,'rgba(255,222,155,0)');c.beginPath();c.moveTo(coords[0].x,y0);coords.forEach((p,i)=>i?c.lineTo(p.x,p.y):c.lineTo(p.x,p.y));c.lineTo(coords[coords.length-1].x,y0);c.closePath();c.fillStyle=grad;c.fill();
 c.beginPath();coords.forEach((p,i)=>{if(i)c.lineTo(p.x,p.y);else c.moveTo(p.x,p.y)});c.strokeStyle='#ffe09e';c.lineWidth=2.5;c.lineJoin='round';c.lineCap='round';c.shadowColor='rgba(255,218,151,.35)';c.shadowBlur=9;c.stroke();c.shadowBlur=0;
 coords.forEach((p,i)=>{c.beginPath();c.arc(p.x,p.y,i===0?4:2.7,0,Math.PI*2);c.fillStyle='#fff2cf';c.fill();c.fillStyle='rgba(255,255,255,.9)';c.font='600 10px DM Sans';c.textAlign='center';let label=chartKind==='rain'?`${Math.round(p.p.value)}%`:chartKind==='wind'?`${Math.round(p.p.value)}`:`${Math.round(p.p.value)}°`;c.fillText(label,p.x,Math.max(10,p.y-10));});
 $('chartLabels').innerHTML=pts.map(p=>`<span>${safe(p.label)}</span>`).join('');
}
document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===btn));chartKind=btn.dataset.kind;drawChart();}));
function toggleSettings(show){$('settingsOverlay').classList.toggle('open',show);}
$('settingsBtn').addEventListener('click',()=>toggleSettings(true));$('closeSettings').addEventListener('click',()=>toggleSettings(false));$('settingsOverlay').addEventListener('click',e=>{if(e.target===$('settingsOverlay'))toggleSettings(false)});
function toggleWorld(show){$('worldOverlay').classList.toggle('open',show);}
$('closeWorld').addEventListener('click',()=>toggleWorld(false));$('worldOverlay').addEventListener('click',e=>{if(e.target===$('worldOverlay'))toggleWorld(false)});
function updateWorldTimeLabel(value){const v=Math.max(0,Math.min(1439,Number(value)||0));$('worldTimeValue').textContent=`${String(Math.floor(v/60)).padStart(2,'0')}:${String(v%60).padStart(2,'0')}`;}
let worldSaveTimer;
function saveWorldControls(){clearTimeout(worldSaveTimer);worldSaveTimer=setTimeout(async()=>{try{await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({visual_time_minutes:Number($('worldTime').value),visual_season:$('worldSeason').value})});await loadState();}catch(e){console.error(e)}},100);}
$('worldTime').addEventListener('input',()=>updateWorldTimeLabel($('worldTime').value));$('worldTime').addEventListener('change',saveWorldControls);$('worldSeason').addEventListener('change',saveWorldControls);
$('worldDefault').addEventListener('click',async()=>{try{await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({visual_time_minutes:null,visual_season:'auto'})});await loadState();}catch(e){console.error(e)}});
window.addEventListener('keydown',e=>{if(e.repeat||e.ctrlKey||e.altKey||e.metaKey)return;const tag=document.activeElement?.tagName||'';if(e.key.toLowerCase()==='y'&&!['INPUT','SELECT','TEXTAREA'].includes(tag)){e.preventDefault();toggleWorld(!$('worldOverlay').classList.contains('open'));}if(e.key==='Escape'){toggleWorld(false);toggleSettings(false);}});

let saveTimer;function saveSettings(){clearTimeout(saveTimer);saveTimer=setTimeout(async()=>{try{await api('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({language:$('languageSelect').value,notify_severe:$('notifySevere').checked,notify_change:$('notifyChange').checked,notify_weather:$('notifyWeather').checked})});await loadState();}catch(e){console.error(e)}},120);}
['languageSelect','notifySevere','notifyChange','notifyWeather'].forEach(id=>$(id).addEventListener('change',saveSettings));
$('refreshBtn').addEventListener('click',async()=>{ $('refreshBtn').style.transform='rotate(180deg)';try{await api('/api/refresh',{method:'POST'});await loadState();}catch(e){console.error(e)}finally{setTimeout(()=>$('refreshBtn').style.transform='',400)}});
async function loadState(){try{const state=await api('/api/state');render(state);}catch(e){console.error(e);}}
window.addEventListener('resize',()=>{drawChart();resizeWeatherCanvas();});

// Automatic seasonal soundtrack. Tracks change with a gentle fade; volume stays at 40%.
const audio=$('seasonAudio');
audio.volume=0; audio.muted=true;
const musicFiles={spring:'весна.mp3',summer:'лето.mp3',autumn:'осень.mp3',winter:'зима.mp3'};
let musicTransition=0, musicReady=false;
function updatePageFavicon(season){
 if(activeFaviconSeason===season)return;
 activeFaviconSeason=season;
 $('pageFavicon').href=`/favicon.ico?season=${encodeURIComponent(season)}&v=${Date.now()}`;
}
function fadeAudio(target,duration,token){
 return new Promise(resolve=>{
  const start=audio.volume,begin=performance.now();
  function step(now){
   if(token!==musicTransition){resolve();return;}
   const p=Math.min(1,(now-begin)/duration);audio.volume=Math.max(0,Math.min(.4,start+(target-start)*p));
   if(p<1)requestAnimationFrame(step);else resolve();
  }
  requestAnimationFrame(step);
 });
}
async function syncMusic(force=false){
 const season=currentSeason||'spring';
 if(!force && musicSeason===season && !audio.paused)return;
 const token=++musicTransition;
 try{
  if(!audio.paused)await fadeAudio(0,850,token);
  if(token!==musicTransition)return;
  audio.pause();
  const filename=musicFiles[season]||musicFiles.spring;
  const next=`/assets/menu/${encodeURIComponent(filename)}`;
  if(!audio.src.endsWith(next)){audio.src=next;audio.load();}
  audio.muted=true;audio.volume=0;
  await audio.play();
  if(token!==musicTransition)return;
  audio.muted=false;musicSeason=season;musicReady=true;
  await fadeAudio(.4,1350,token);
 }catch(error){
  // A browser may postpone autoplay until the embedded page becomes active.
  audio.muted=true;audio.volume=0;musicReady=false;
 }
}
window.addEventListener('focus',()=>{if(audio.paused)syncMusic(true);});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&audio.paused)syncMusic(true);});
window.addEventListener('pointerdown',()=>{if(audio.paused)syncMusic(true);},{once:true});

// Seasonal objects: the shovel/broom are physical draggable objects near the trees, not toolbar buttons.
const sky=$('sky'),skyCtx=sky.getContext('2d'),weatherCanvas=$('weatherCanvas'),ctx=weatherCanvas.getContext('2d');
const seasonTool=$('seasonTool');
let particles=[],leaves=[],stars=[],lastFrame=0,windPhase=0,lastGust=0;
let snowTerrain=[],snowChunks=[],shovelLoad=0,leafPileCount=0;
let toolState={x:0,y:0,vx:0,vy:0,angle:0,av:0,dragging:false,offsetX:0,offsetY:0,lastX:0,lastY:0};
const FLOOR_MARGIN=5;
function resizeWeatherCanvas(){$('snowSvg').setAttribute('viewBox',`0 0 ${innerWidth} ${innerHeight}`);const dpr=Math.min(devicePixelRatio||1,2);weatherCanvas.width=Math.floor(innerWidth*dpr);weatherCanvas.height=Math.floor(innerHeight*dpr);weatherCanvas.style.width=innerWidth+'px';weatherCanvas.style.height=innerHeight+'px';ctx.setTransform(dpr,0,0,dpr,0,0);sky.width=Math.floor(innerWidth*dpr);sky.height=Math.floor(innerHeight*dpr);skyCtx.setTransform(dpr,0,0,dpr,0,0);initSnowTerrain();drawSnowSvg();}
function rand(a,b){return a+Math.random()*(b-a)}
function makeParticle(){const snow=currentSeason==='winter';return{x:rand(0,innerWidth),y:rand(-innerHeight,innerHeight),r:snow?rand(1.3,4.1):rand(1,2.2),vy:snow?rand(.35,1.45):rand(5,10),vx:rand(-.35,.35),alpha:rand(.3,.85),sway:rand(0,Math.PI*2)};}
function makeLeaf(){return{x:rand(0,innerWidth),y:rand(-innerHeight,0),size:rand(7,14),vy:rand(.6,1.6),vx:rand(-.8,.8),rot:rand(0,6.28),spin:rand(-.035,.035),phase:rand(0,6.28),hue:rand(15,39),alpha:rand(.65,.95),resting:false};}
function initSnowTerrain(){const count=Math.max(16,Math.ceil(innerWidth/24));const old=snowTerrain;if(currentSeason==='winter'&&old.length){snowTerrain=Array.from({length:count},(_,i)=>Math.max(0,old[Math.round(i*(old.length-1)/Math.max(1,count-1))]||0));}else snowTerrain=Array(count).fill(0);}
function resetAmbient(){particles=[];leaves=[];snowChunks=[];const count=currentSeason==='winter'?125:currentSeason==='autumn'?18:48;for(let i=0;i<count;i++)particles.push(makeParticle());if(currentSeason==='autumn')for(let i=0;i<40;i++)leaves.push(makeLeaf());initSnowTerrain();}
function setToolMarkup(season){
 if(season==='winter'){
  seasonTool.className='world-tool visible shovel';seasonTool.title='Drag the shovel';
  seasonTool.innerHTML=`<svg viewBox="0 0 90 140" aria-hidden="true"><defs><linearGradient id="shovelMetal" x2="1" y2="1"><stop stop-color="#f8fdff"/><stop offset=".5" stop-color="#a8c5d8"/><stop offset="1" stop-color="#6d8da5"/></linearGradient></defs><path d="M43 12 Q45 7 50 10 L54 92 Q53 99 47 99 L42 95Z" fill="#8b5a3b" stroke="#d4a67b" stroke-width="3"/><path d="M34 8 Q47 -1 59 9 Q58 18 48 19 Q36 19 34 8Z" fill="#5a3828" stroke="#d4a67b" stroke-width="2"/><path d="M26 89 Q45 98 65 89 L78 119 Q47 135 13 119Z" fill="url(#shovelMetal)" stroke="#e9f7ff" stroke-width="3"/><path d="M22 115 Q46 125 70 115" fill="none" stroke="#fff" stroke-opacity=".7" stroke-width="3"/></svg>`;
  seasonTool.style.width='76px';seasonTool.style.height='116px';
 }else if(season==='autumn'){
  seasonTool.className='world-tool visible broom';seasonTool.title='Drag the broom';
  seasonTool.innerHTML=`<svg viewBox="0 0 150 90" aria-hidden="true"><path d="M20 12 L103 60" stroke="#8e5b38" stroke-width="9" stroke-linecap="round"/><path d="M12 9 L24 16" stroke="#e6c08e" stroke-width="13" stroke-linecap="round"/><path d="M87 50 Q107 49 130 61 L143 76 Q112 85 91 68Z" fill="#d69a4f" stroke="#f3c47c" stroke-width="3"/><path d="M94 55 L105 79 M104 59 L115 82 M115 62 L125 82 M125 65 L135 78" stroke="#8b542d" stroke-width="2"/></svg>`;
  seasonTool.style.width='126px';seasonTool.style.height='76px';
 }else{seasonTool.className='world-tool';seasonTool.innerHTML='';}
}
function toolFloorY(x){const snow=currentSeason==='winter'?heightAt(x):0;return innerHeight-snow-seasonTool.offsetHeight-FLOOR_MARGIN;}
function placeToolForSeason(){setToolMarkup(currentSeason);toolState.dragging=false;toolState.vx=0;toolState.vy=0;toolState.av=0;if(currentSeason==='winter'){toolState.x=Math.max(28,innerWidth*.17);toolState.y=toolFloorY(toolState.x);toolState.angle=-.28;}else if(currentSeason==='autumn'){toolState.x=Math.max(30,innerWidth*.77);toolState.y=toolFloorY(toolState.x);toolState.angle=-.18;}if(seasonTool.classList.contains('visible'))renderTool();}
function renderTool(){seasonTool.style.transform=`translate3d(${toolState.x}px,${toolState.y}px,0) rotate(${toolState.angle}rad)`;}
function updateSeasonDecor(){document.body.classList.remove('winter','spring','summer','autumn');document.body.classList.add(currentSeason);if(decorSeason!==currentSeason){decorSeason=currentSeason;resetAmbient();placeToolForSeason();syncMusic(true);updatePageFavicon(currentSeason);}}
function heightAt(x){if(!snowTerrain.length)return 0;const idx=Math.max(0,Math.min(snowTerrain.length-1,Math.round(x/(innerWidth||1)*(snowTerrain.length-1))));return snowTerrain[idx]||0;}
function drawSnowSvg(){if(currentSeason!=='winter'||!snowTerrain.length){$('snowShape').setAttribute('d','');$('snowShadow').setAttribute('d','');return;}const width=innerWidth,height=innerHeight,n=snowTerrain.length,step=width/(n-1);let d=`M 0 ${height} L 0 ${height-snowTerrain[0]}`;for(let i=1;i<n;i++){const x=i*step,y=height-snowTerrain[i],px=(i-.5)*step,py=height-(snowTerrain[i-1]+snowTerrain[i])*.5;d+=` Q ${px} ${py} ${x} ${y}`;}d+=` L ${width} ${height} Z`;$('snowShape').setAttribute('d',d);$('snowShadow').setAttribute('d',d);}
function terrainAdd(x,amount){if(!snowTerrain.length)return;const i=Math.max(0,Math.min(snowTerrain.length-1,Math.round(x/(innerWidth||1)*(snowTerrain.length-1))));snowTerrain[i]=Math.min(118,(snowTerrain[i]||0)+amount);}
function digSnow(x,y,dx){if(!snowTerrain.length||y<innerHeight-155)return;const i=Math.max(0,Math.min(snowTerrain.length-1,Math.round(x/(innerWidth||1)*(snowTerrain.length-1))));const surface=innerHeight-snowTerrain[i];if(y<surface-48)return;let amount=Math.min(snowTerrain[i],Math.max(0.8,Math.abs(dx)*.1+1.6));snowTerrain[i]=Math.max(0,snowTerrain[i]-amount);shovelLoad+=amount;const push=Math.max(1,Math.round((dx>=0?1:-1)*2));const dest=Math.max(0,Math.min(snowTerrain.length-1,i+push));snowTerrain[dest]=Math.min(118,snowTerrain[dest]+amount*.56);drawSnowSvg();}
function releaseSnow(){if(shovelLoad>5){snowChunks.push({x:toolState.x+37,y:toolState.y+34,r:Math.min(16,5+Math.sqrt(shovelLoad)),vx:toolState.vx*.3+rand(-.7,.7),vy:-1.1,mass:shovelLoad*.42});}shovelLoad=0;}
function sweepLeaves(x,y,dx){if(y<innerHeight-130)return;for(const l of leaves){if(l.resting&&Math.hypot(l.x-x,l.y-y)<74){l.resting=false;l.vx=(dx===0?1:Math.sign(dx))*rand(1.4,3.4);l.vy=-rand(.5,1.4);l.spin=rand(-.08,.08);}}}
function sweepAt(x,y,dx){if(currentSeason==='winter')digSnow(x,y,dx);else if(currentSeason==='autumn')sweepLeaves(x,y,dx);}
seasonTool.addEventListener('pointerdown',e=>{if(!seasonTool.classList.contains('visible'))return;e.preventDefault();e.stopPropagation();toolState.dragging=true;toolState.vx=0;toolState.vy=0;toolState.offsetX=e.clientX-toolState.x;toolState.offsetY=e.clientY-toolState.y;toolState.lastX=e.clientX;toolState.lastY=e.clientY;seasonTool.classList.add('dragging');seasonTool.setPointerCapture(e.pointerId);});
seasonTool.addEventListener('pointermove',e=>{if(!toolState.dragging)return;const nx=e.clientX-toolState.offsetX,ny=e.clientY-toolState.offsetY,dx=nx-toolState.x,dy=ny-toolState.y;toolState.x=nx;toolState.y=ny;toolState.angle=Math.max(-.8,Math.min(.8,toolState.angle+dx*.006+dy*.002));toolState.vx=dx*.55;toolState.vy=dy*.35;sweepAt(e.clientX,e.clientY,dx);toolState.lastX=e.clientX;toolState.lastY=e.clientY;renderTool();});
function endToolDrag(){if(!toolState.dragging)return;toolState.dragging=false;seasonTool.classList.remove('dragging');if(currentSeason==='winter')releaseSnow();}
seasonTool.addEventListener('pointerup',endToolDrag);seasonTool.addEventListener('pointercancel',endToolDrag);
function updateToolPhysics(dt){if(!seasonTool.classList.contains('visible')||toolState.dragging)return;const k=dt/16.67;toolState.vy+=.23*k;toolState.x+=toolState.vx*k;toolState.y+=toolState.vy*k;toolState.angle+=toolState.av*k;toolState.vx*=Math.pow(.985,k);toolState.av*=Math.pow(.94,k);const floor=toolFloorY(toolState.x);if(toolState.y>floor){toolState.y=floor;if(toolState.vy>1.1){toolState.vy*=-.22;toolState.av+=rand(-.012,.012);}else toolState.vy=0;toolState.vx*=.72;if(Math.abs(toolState.vx)<.025)toolState.vx=0;if(Math.abs(toolState.av)<.001)toolState.av=0;}toolState.x=Math.max(-25,Math.min(innerWidth-seasonTool.offsetWidth+15,toolState.x));renderTool();}
function updateSnowChunks(dt){const k=dt/16.67;for(let i=snowChunks.length-1;i>=0;i--){const c=snowChunks[i];c.vy+=.22*k;c.x+=c.vx*k;c.y+=c.vy*k;if(c.y>=innerHeight-heightAt(c.x)-c.r){terrainAdd(c.x,c.mass);snowChunks.splice(i,1);}}}
function relaxSnow(){if(currentSeason!=='winter')return;for(let i=1;i<snowTerrain.length-1;i++){const avg=(snowTerrain[i-1]+snowTerrain[i+1])*.5,diff=snowTerrain[i]-avg;if(Math.abs(diff)>9)snowTerrain[i]-=Math.sign(diff)*Math.min(1.2,Math.abs(diff)*.035);}drawSnowSvg();}
function drawSky(){skyCtx.clearRect(0,0,innerWidth,innerHeight);const night=appState?.phase==='night';if(night){for(const s of stars){skyCtx.globalAlpha=s.a;skyCtx.fillStyle='#fff';skyCtx.beginPath();skyCtx.arc(s.x,s.y,s.r,0,Math.PI*2);skyCtx.fill();s.a+=s.d;if(s.a>.9||s.a<.2)s.d*=-1;}skyCtx.globalAlpha=1;const moon=skyCtx.createRadialGradient(innerWidth*.78,innerHeight*.13,2,innerWidth*.78,innerHeight*.13,65);moon.addColorStop(0,'rgba(230,239,255,.23)');moon.addColorStop(1,'rgba(230,239,255,0)');skyCtx.fillStyle=moon;skyCtx.fillRect(innerWidth*.78-65,innerHeight*.13-65,130,130);skyCtx.fillStyle='rgba(238,244,255,.8)';skyCtx.beginPath();skyCtx.arc(innerWidth*.78,innerHeight*.13,18,0,Math.PI*2);skyCtx.fill();skyCtx.fillStyle='#1c2d4b';skyCtx.beginPath();skyCtx.arc(innerWidth*.78+8,innerHeight*.13-5,17,0,Math.PI*2);skyCtx.fill();}}
function drawWeather(now){ctx.clearRect(0,0,innerWidth,innerHeight);const isWinter=currentSeason==='winter',isAutumn=currentSeason==='autumn',code=appState?.weather?.code||0;const rain=[51,53,55,56,57,61,63,65,66,67,80,81,82,95,96,99].includes(code);
 for(let i=particles.length-1;i>=0;i--){const p=particles[i];p.sway+=.012;p.x+=p.vx+Math.sin(p.sway)*.26+(isAutumn?.24:0);p.y+=p.vy+(isWinter?0:rain?2.6:0);if(p.y>innerHeight+8){particles[i]=makeParticle();particles[i].y=-8;continue;}if(isWinter&&p.y>=innerHeight-heightAt(p.x)-p.r){terrainAdd(p.x,p.r*.20);particles[i]=makeParticle();particles[i].y=-8;continue;}ctx.globalAlpha=p.alpha;if(isWinter){ctx.fillStyle='#fff';ctx.shadowColor='rgba(230,246,255,.8)';ctx.shadowBlur=8;ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;}else if(rain){ctx.strokeStyle='rgba(208,231,255,.48)';ctx.lineWidth=1.1;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(p.x-3,p.y+12);ctx.stroke();}else{ctx.fillStyle='rgba(255,255,255,.24)';ctx.beginPath();ctx.arc(p.x,p.y,p.r*.65,0,Math.PI*2);ctx.fill();}ctx.globalAlpha=1;}
 if(isAutumn){for(let i=leaves.length-1;i>=0;i--){const l=leaves[i];if(!l.resting){l.phase+=.02;l.x+=l.vx+Math.sin(l.phase)*.75;l.y+=l.vy;l.rot+=l.spin;l.vy+=.035;if(l.y>=innerHeight-l.size*.45){l.y=innerHeight-l.size*.45;l.vy=0;l.vx=0;l.resting=true;l.spin=0;}}ctx.save();ctx.translate(l.x,l.y);ctx.rotate(l.rot);ctx.globalAlpha=l.alpha;ctx.fillStyle=`hsl(${l.hue},76%,${l.resting?43:43+Math.sin(l.phase)*5}%)`;ctx.beginPath();ctx.moveTo(0,-l.size);ctx.quadraticCurveTo(l.size*1.2,-l.size*.2,0,l.size);ctx.quadraticCurveTo(-l.size*1.2,-l.size*.2,0,-l.size);ctx.fill();ctx.strokeStyle='rgba(255,220,170,.3)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(0,-l.size*.7);ctx.lineTo(0,l.size*.75);ctx.stroke();ctx.restore();}if(leaves.length<42&&Math.random()<.045)leaves.push(makeLeaf());}
 if(isWinter){for(const c of snowChunks){ctx.save();ctx.fillStyle='rgba(245,252,255,.96)';ctx.shadowColor='rgba(235,248,255,.6)';ctx.shadowBlur=9;ctx.beginPath();ctx.arc(c.x,c.y,c.r,0,Math.PI*2);ctx.fill();ctx.restore();}}
 windPhase+=.002;if(isAutumn&&appState?.phase==='night'&&Math.sin(windPhase)>0.997&&now-lastGust>45000){lastGust=now;leaves.forEach(l=>{if(l.resting){l.resting=false;l.vx=rand(2,5);l.vy=rand(.5,1.3);l.spin=rand(-.08,.08);}});}
}
function frame(now){if(!lastFrame)lastFrame=now;const dt=Math.min(40,now-lastFrame);lastFrame=now;updateToolPhysics(dt);updateSnowChunks(dt);if(currentSeason==='winter'&&appState?.phase==='day'&&snowTerrain.length){for(let i=0;i<snowTerrain.length;i++)snowTerrain[i]=Math.max(0,snowTerrain[i]-dt*.00009);}
 if(currentSeason==='winter')relaxSnow();drawSky();drawWeather(now);requestAnimationFrame(frame);}
for(let i=0;i<90;i++)stars.push({x:rand(0,innerWidth),y:rand(0,innerHeight*.65),r:rand(.4,1.5),a:rand(.2,.8),d:rand(.002,.008)*(Math.random()<.5?-1:1)});resizeWeatherCanvas();resetAmbient();requestAnimationFrame(frame);

loadState();setInterval(loadState,STATE_REFRESH_SECONDS*1000);setInterval(()=>{if(appState){setText('clock',appState.clock||'--:--');}},5000);
</script>
</body></html>'''


# ---------------------------------------------------------------------------
# Secure, localhost-only HTTP server
# ---------------------------------------------------------------------------
class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(app: PluxApp):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "PluxLocal/4"

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("HTTP %s - %s", self.address_string(), fmt % args)

        def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            if path == "/":
                self.send_bytes(200, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
                return
            if path == "/api/state":
                body = json.dumps(app.state(), ensure_ascii=False).encode("utf-8")
                self.send_bytes(200, "application/json; charset=utf-8", body)
                return
            if path == "/favicon.ico":
                season = app.state().get("season", "spring")
                icon_name = "иконка зима.ico" if season == "winter" else "иконка лето и другие.ico"
                icon_path = resource_path(icon_name)
                if icon_path.is_file():
                    try:
                        self.send_bytes(200, "image/x-icon", icon_path.read_bytes())
                        return
                    except OSError:
                        pass
                self.send_error(404)
                return
            if path.startswith("/assets/"):
                relative = path[len("/assets/"):]
                allowed_dirs = {"menu", "warning"}
                parts = Path(relative).parts
                if len(parts) == 2 and parts[0] in allowed_dirs and ".." not in parts:
                    folder_name = "меню" if parts[0] == "menu" else "warning"
                    file_path = resource_path(str(Path(folder_name) / parts[1]))
                    if file_path.is_file():
                        suffix = file_path.suffix.lower()
                        mime = {".mp3": "audio/mpeg", ".png": "image/png", ".ico": "image/x-icon"}.get(suffix, "application/octet-stream")
                        try:
                            self.send_bytes(200, mime, file_path.read_bytes())
                        except OSError:
                            self.send_error(404)
                        return
                self.send_error(404)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length < 0 or length > 64_000:
                self.send_error(413)
                return
            if path == "/api/settings":
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    values = json.loads(raw or "{}")
                    if not isinstance(values, dict):
                        raise ValueError("Settings must be an object")
                    app.update_settings(values)
                    self.send_bytes(200, "application/json; charset=utf-8", b'{"ok":true}')
                except Exception as exc:
                    log.warning("Invalid settings request: %s", exc)
                    self.send_error(400)
                return
            if path == "/api/refresh":
                app.request_refresh()
                self.send_bytes(200, "application/json; charset=utf-8", b'{"ok":true}')
                return
            self.send_error(404)

    return Handler


# ---------------------------------------------------------------------------
# Windows tray, hotkey and optional transparent pywebview window
# ---------------------------------------------------------------------------
def tray_image(season: str = "spring"):
    try:
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((5, 8, 57, 60), fill=(18, 39, 62, 235), outline=(255, 255, 255, 200), width=2)
        if season == "winter":
            draw.ellipse((17, 19, 47, 49), fill=(224, 244, 255, 255))
            draw.line((32, 15, 32, 53), fill=(255, 255, 255, 255), width=3)
            draw.line((15, 34, 49, 34), fill=(255, 255, 255, 255), width=3)
        elif season == "autumn":
            draw.ellipse((18, 14, 46, 48), fill=(235, 137, 65, 255))
            draw.line((23, 43, 43, 20), fill=(255, 222, 157, 255), width=2)
        else:
            draw.ellipse((16, 15, 47, 46), fill=(255, 205, 92, 255))
            draw.ellipse((27, 26, 54, 50), fill=(233, 241, 246, 255))
        return image
    except Exception:
        return None


def create_tray(app: PluxApp, show_callback, exit_callback) -> None:
    """Create a stable tray icon; seasonal .ico files are reserved for the webpage favicon."""
    try:
        import pystray
        icon_image = tray_image("spring")
        if icon_image is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Open Plux", lambda: show_callback(), default=True),
            pystray.MenuItem("Refresh weather", lambda: app.request_refresh()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda: exit_callback()),
        )
        app.tray_icon = pystray.Icon("PluxWeather", icon_image, APP_NAME, menu)
        threading.Thread(target=app.tray_icon.run, name="PluxTray", daemon=True).start()
    except Exception as exc:
        log.info("System tray unavailable (install pystray and pillow): %s", exc)


def hotkey_loop(app: PluxApp, show_callback) -> None:
    """Reliable global double-tap Ctrl+Shift detector (press, release, press)."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        VK_CONTROL, VK_SHIFT = 0x11, 0x10
        previous_combo = False
        taps: list[float] = []
        last_open = 0.0
        while not app.stop_event.is_set():
            ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            shift_down = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            combo = ctrl_down and shift_down
            if combo and not previous_combo:
                now = time.monotonic()
                taps = [stamp for stamp in taps if now - stamp <= HOTKEY_WINDOW_SECONDS]
                taps.append(now)
                if len(taps) >= 2 and now - last_open > 1.0:
                    taps.clear()
                    last_open = now
                    show_callback()
            previous_combo = combo
            time.sleep(0.015)
    except Exception:
        log.exception("Global hotkey loop stopped")


def run_pywebview(app: PluxApp, show_event: threading.Event) -> None:
    """Optional transparent native window. Browser fallback is used when pywebview is absent."""
    try:
        import webview
        window = webview.create_window(
            APP_NAME,
            f"http://127.0.0.1:{app.port}/",
            width=1160,
            height=850,
            min_size=(720, 620),
            resizable=True,
            frameless=False,
            easy_drag=False,
            hidden=True,
            transparent=True,
            background_color="#081426",
        )
        app.webview_window = window
        window_loaded = threading.Event()
        try:
            window.events.loaded += lambda *_: window_loaded.set()
        except Exception:
            # Some pywebview versions don't expose the loaded event consistently.
            window_loaded.set()

        def show_monitor() -> None:
            while not app.stop_event.is_set():
                if not show_event.wait(.2):
                    continue
                # Keep the request pending until WebView2 has completed its first load.
                if not window_loaded.wait(.15):
                    continue
                show_event.clear()
                try:
                    window.show()
                    try:
                        window.restore()
                    except Exception:
                        pass
                except Exception:
                    log.exception("Could not show Plux window")
        threading.Thread(target=show_monitor, name="PluxShowMonitor", daemon=True).start()
        def keep_running_on_close():
            try:
                window.hide()
            except Exception:
                pass
            return False
        window.events.closing += keep_running_on_close
        webview.start(debug=False, gui="edgechromium")
    except ImportError:
        log.info("pywebview not installed; using browser window")
        while not app.stop_event.is_set():
            if show_event.wait(.25):
                show_event.clear()
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{app.port}/", new=2)
    except Exception as exc:
        log.exception("pywebview startup failed: %s", exc)
        while not app.stop_event.is_set():
            if show_event.wait(.25):
                show_event.clear()
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{app.port}/", new=2)


def hide_console() -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def main() -> None:
    if sys.platform == "win32":
        hide_console()
    app = PluxApp()
    show_event = threading.Event()
    exit_event = threading.Event()

    def show_ui() -> None:
        show_event.set()

    def quit_app() -> None:
        exit_event.set()
        app.stop_event.set()
        show_event.set()
        if app.tray_icon:
            try:
                app.tray_icon.stop()
            except Exception:
                pass
        if app.server:
            threading.Thread(target=app.server.shutdown, daemon=True).start()

    # Bind only to loopback: the local weather UI is not exposed to the network.
    app.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    app.port = int(app.server.server_address[1])
    threading.Thread(target=app.server.serve_forever, name="PluxHTTP", daemon=True).start()
    threading.Thread(target=app.poll_loop, name="PluxWeatherPoll", daemon=True).start()
    threading.Thread(target=hotkey_loop, args=(app, show_ui), name="PluxHotkey", daemon=True).start()
    create_tray(app, show_ui, quit_app)

    # The polling thread performs the first weather update immediately. The UI stays hidden until opened.

    if sys.platform == "win32":
        try:
            run_pywebview(app, show_event)
        except KeyboardInterrupt:
            pass
    else:
        log.info("Plux is running. Open http://127.0.0.1:%s/ to view it.", app.port)
        try:
            while not exit_event.wait(1):
                pass
        except KeyboardInterrupt:
            pass
    quit_app()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Fatal Plux error")
        raise
