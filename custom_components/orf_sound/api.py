"""Asynchroner API-Client fuer ORF Radimeta und APA Loopstream."""

import datetime
import logging
import re
import urllib.parse
import aiohttp

from .const import DEFAULT_USER_AGENT, STATIONS

_LOGGER = logging.getLogger(__name__)

WEEKDAYS = {
    "mo": 0, "montag": 0, "monday": 0,
    "di": 1, "dienstag": 1, "tuesday": 1,
    "mi": 2, "mittwoch": 2, "wednesday": 2,
    "do": 3, "donnerstag": 3, "thursday": 3,
    "fr": 4, "freitag": 4, "friday": 4,
    "sa": 5, "samstag": 5, "saturday": 5,
    "so": 6, "sonntag": 6, "sunday": 6,
}

GERMAN_WEEKDAYS = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]


def parse_datetime_input(text: str, now: datetime.datetime = None) -> datetime.datetime:
    """Parst natuerliche und absolute Zeitangaben in ein timezone-aware datetime."""
    if now is None:
        now = datetime.datetime.now().astimezone()

    s = text.strip().lower()
    s = re.sub(r"\buhr\b", "", s).strip()

    # 1. ISO 8601 (z.B. 2026-10-11T06:00:00 oder 2026-10-11 06:00)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.replace(tzinfo=now.tzinfo)
        except ValueError:
            pass

    # 2. Deutsches Format (11.10.2026 06:00 oder 11.10. 06:00)
    m = re.match(
        r"^(\d{1,2})\.(\d{1,2})\.(?:(\d{4})\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?$",
        s,
    )
    if m:
        day, month, year, hour, minute, second = m.groups()
        year = int(year) if year else now.year
        second = int(second) if second else 0
        dt = datetime.datetime(year, int(month), int(day), int(hour), int(minute), second)
        return dt.replace(tzinfo=now.tzinfo)

    # 3. Wochentag oder Heute/Gestern + Uhrzeit (z.B. "Sonntag 06:00", "Gestern 14:00")
    m_day = re.match(r"^([a-zäöü]+)\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m_day:
        day_word, hour, minute, second = m_day.groups()
        hour = int(hour)
        minute = int(minute)
        second = int(second) if second else 0

        target_date = None
        if day_word in ("heute", "today"):
            target_date = now.date()
        elif day_word in ("gestern", "yesterday"):
            target_date = now.date() - datetime.timedelta(days=1)
        elif day_word in WEEKDAYS:
            target_weekday = WEEKDAYS[day_word]
            # Rueckwaerts zum letzten passenden Wochentag (0 bis 6 Tage zurueck)
            days_back = (now.weekday() - target_weekday) % 7
            target_date = now.date() - datetime.timedelta(days=days_back)
        else:
            raise ValueError(f"Unbekannte Tagesangabe: '{day_word}'")

        dt = datetime.datetime(target_date.year, target_date.month, target_date.day, hour, minute, second)
        dt = dt.replace(tzinfo=now.tzinfo)

        # Wenn heute der Wochentag ist und die Uhrzeit noch in der Zukunft liegt, nimm Vorwoche
        if dt > now and day_word in WEEKDAYS:
            dt -= datetime.timedelta(days=7)
        return dt

    # 4. Reine Uhrzeit (z.B. "06:00", "6:00")
    m_time = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m_time:
        hour, minute, second = m_time.groups()
        hour = int(hour)
        minute = int(minute)
        second = int(second) if second else 0
        dt = datetime.datetime(now.year, now.month, now.day, hour, minute, second, tzinfo=now.tzinfo)
        # Wenn Uhrzeit heute noch in der Zukunft liegt, nimm gestern
        if dt > now:
            dt -= datetime.timedelta(days=1)
        return dt

    raise ValueError(
        f"Ungueltiges Datums-/Zeitformat: '{text}'. Beispiele: 'Sonntag 6:00 Uhr', '06:00', 'gestern 18:00'"
    )


class OrfSoundClient:
    """Asynchroner API-Client fuer die ORF Sound Plattform."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def resolve_timeshift_stream(
        self,
        station_key: str,
        target_dt: datetime.datetime,
        duration_minutes: int = None,
    ) -> dict:
        """Ermittelt Sendung und generiert die passende APA Loopstream-URL."""
        if station_key not in STATIONS:
            raise ValueError(f"Unbekannter Sender '{station_key}'. Erlaubt: {list(STATIONS.keys())}")

        station_info = STATIONS[station_key]
        target_dt_utc = target_dt.astimezone(datetime.timezone.utc)
        target_ms = int(target_dt_utc.timestamp() * 1000)

        headers = {"User-Agent": DEFAULT_USER_AGENT}

        # 1. 7-Tage Sendeplan abfragen
        broadcasts_url = f"https://audioapi.orf.at/{station_key}/api/json/4.0/broadcasts"
        _LOGGER.debug("Rufe ORF Sendeplan ab: %s", broadcasts_url)

        async with self._session.get(broadcasts_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Fehler beim Abrufen des Sendeplans (HTTP {resp.status})")
            days = await resp.json()

        # Sendung ermitteln, die target_ms umfasst
        matched_broadcast = None
        for day_entry in days:
            for b in (day_entry.get("broadcasts") or []):
                b_start = b.get("start")
                b_end = b.get("end")
                if b_start is not None and b_end is not None:
                    if b_start <= target_ms <= b_end:
                        matched_broadcast = b
                        break
            if matched_broadcast:
                break

        if not matched_broadcast:
            raise RuntimeError(
                f"Keine Sendung fuer den Zeitpunkt {target_dt.strftime('%d.%m.%Y %H:%M:%S')} gefunden. "
                "Liegt er innerhalb der letzten 7 Tage?"
            )

        # 2. Sendungsdetails abrufen
        detail_url = matched_broadcast.get("href")
        if not detail_url:
            detail_url = f"https://audioapi.orf.at/{station_key}/api/json/4.0/broadcast/{matched_broadcast['programKey']}/{matched_broadcast['broadcastDay']}"

        _LOGGER.debug("Rufe Sendungsdetails ab: %s", detail_url)
        async with self._session.get(detail_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Fehler beim Abrufen der Sendungsdetails (HTTP {resp.status})")
            detail_data = await resp.json()

        streams = detail_data.get("streams") or []
        if not streams or not isinstance(streams, list) or not streams[0].get("loopStreamId"):
            raise RuntimeError("In den Sendungsdetails wurden keine gueltigen Stream-Informationen gefunden.")

        stream_info = streams[0]
        loopstream_id = stream_info["loopStreamId"]
        stream_start_ms = stream_info.get("start", matched_broadcast.get("start"))

        # 3. Offset in Millisekunden berechnen
        offset_ms = max(0, target_ms - stream_start_ms)

        # 4. Loopstream URL zusammenbauen
        channel = station_info["channel"]
        host = station_info["loopstream_host"]
        params = {
            "channel": channel,
            "id": loopstream_id,
            "offset": offset_ms,
            "shoutcast": 0,
        }

        if duration_minutes is not None and duration_minutes > 0:
            offsetende_ms = offset_ms + (duration_minutes * 60 * 1000)
            params["offsetende"] = offsetende_ms

        day_name = GERMAN_WEEKDAYS[target_dt.weekday()]
        time_str = target_dt.strftime("%H:%M")
        station_name = station_info["name"]
        broadcast_title = matched_broadcast.get("title") or station_name

        # Display-Titel formatieren: Sender | Wochentag HH:MM Uhr [| Sendung]
        clean_broadcast_title = re.sub(r'[/\\?#&"<>*|]', " ", broadcast_title).strip()
        clean_broadcast_title = re.sub(r"\s+", " ", clean_broadcast_title)

        if clean_broadcast_title and clean_broadcast_title.lower() != station_name.lower():
            display_title = f"{station_name} | {day_name} {time_str} Uhr | {clean_broadcast_title}"
        else:
            display_title = f"{station_name} | {day_name} {time_str} Uhr"

        # Stream-URL mit Fragment-Dateinamen fuer Music Assistant, DLNA und Player
        # Sorgt dafuer, dass Yamaha MusicCast und Music Assistant den sauberen Titel anzeigen
        stream_url = f"https://{host}/?{urllib.parse.urlencode(params)}#/{display_title}.mp3"

        # Bild ermitteln (Cover oder Sendungslogo)
        image_url = None
        all_images = (matched_broadcast.get("images") or []) + (detail_data.get("images") or [])
        for img in all_images:
            if not isinstance(img, dict):
                continue
            versions = img.get("versions") or []
            if isinstance(versions, list) and len(versions) > 0:
                v = versions[0]
                if isinstance(v, dict) and "path" in v:
                    image_url = v["path"]
                    break

        return {
            "station_key": station_key,
            "station_name": station_info["name"],
            "title": matched_broadcast.get("title", station_info["name"]),
            "display_title": display_title,
            "day_name": day_name,
            "time_str": time_str,
            "subtitle": matched_broadcast.get("subtitle"),
            "moderator": matched_broadcast.get("moderator"),
            "program_key": matched_broadcast.get("programKey"),
            "start_iso": matched_broadcast.get("startISO"),
            "end_iso": matched_broadcast.get("endISO"),
            "target_time_local": target_dt.isoformat(),
            "target_time_utc": target_dt_utc.isoformat(),
            "offset_ms": offset_ms,
            "loopstream_id": loopstream_id,
            "stream_url": stream_url,
            "image_url": image_url,
        }
