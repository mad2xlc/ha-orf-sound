"""Home Assistant Integration fuer ORF Sound Timeshift Streaming."""

from __future__ import annotations

import datetime
import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
)

# Abwaertskompatible und robuste Imports fuer HA-Versionen
try:
    from homeassistant.components.media_player.const import ATTR_MEDIA_EXTRA
except ImportError:
    ATTR_MEDIA_EXTRA = "extra"

try:
    from homeassistant.components.media_player.const import MediaType
    MEDIA_TYPE_MUSIC = MediaType.MUSIC
except (ImportError, AttributeError):
    MEDIA_TYPE_MUSIC = "music"

try:
    from homeassistant.core import SupportsResponse
    SUPPORTS_RESPONSE = SupportsResponse.ONLY
except (ImportError, AttributeError):
    SUPPORTS_RESPONSE = None

from .api import OrfSoundClient, parse_datetime_input
from .const import (
    DOMAIN,
    SERVICE_PLAY_TIMESHIFT,
    SERVICE_GET_STREAM_URL,
    ATTR_STATION,
    ATTR_TIME,
    ATTR_HOURS_AGO,
    ATTR_MINUTES_AGO,
    ATTR_DURATION_MINUTES,
    STATIONS,
)

_LOGGER = logging.getLogger(__name__)

PLAY_TIMESHIFT_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(ATTR_STATION): vol.In(list(STATIONS.keys())),
        vol.Optional(ATTR_TIME): cv.string,
        vol.Optional(ATTR_HOURS_AGO): vol.Coerce(float),
        vol.Optional(ATTR_MINUTES_AGO): vol.Coerce(float),
        vol.Optional(ATTR_DURATION_MINUTES): cv.positive_int,
    }
)

GET_STREAM_URL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_STATION): vol.In(list(STATIONS.keys())),
        vol.Optional(ATTR_TIME): cv.string,
        vol.Optional(ATTR_HOURS_AGO): vol.Coerce(float),
        vol.Optional(ATTR_MINUTES_AGO): vol.Coerce(float),
        vol.Optional(ATTR_DURATION_MINUTES): cv.positive_int,
    }
)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Registriert die ORF Sound Dienste, falls noch nicht registriert."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_TIMESHIFT):
        return

    session = async_get_clientsession(hass)
    client = OrfSoundClient(session)

    async def _resolve_from_call(call: ServiceCall) -> dict:
        station = call.data[ATTR_STATION]
        time_str = call.data.get(ATTR_TIME)
        hours_ago = call.data.get(ATTR_HOURS_AGO)
        minutes_ago = call.data.get(ATTR_MINUTES_AGO, 0.0)
        duration = call.data.get(ATTR_DURATION_MINUTES)

        now = datetime.datetime.now().astimezone()

        if time_str:
            target_dt = parse_datetime_input(time_str, now=now)
        else:
            h = hours_ago if hours_ago is not None else 1.0
            total_sec = (h * 3600) + (minutes_ago * 60)
            target_dt = now - datetime.timedelta(seconds=total_sec)

        return await client.resolve_timeshift_stream(
            station_key=station,
            target_dt=target_dt,
            duration_minutes=duration,
        )

    async def handle_play_timeshift(call: ServiceCall) -> None:
        """Service Handler: Loest den Stream auf und startet ihn auf dem Ziel-Player."""
        stream_data = await _resolve_from_call(call)
        entity_ids = call.data["entity_id"]
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        stream_url = stream_data["stream_url"]
        title = stream_data.get("title", stream_data["station_name"])
        artist = stream_data["station_name"]
        image = stream_data.get("image_url")

        _LOGGER.info(
            "Starte ORF Timeshift (%s: '%s') auf %s: %s",
            artist,
            title,
            entity_ids,
            stream_url,
        )

        media_service_data = {
            ATTR_MEDIA_CONTENT_ID: stream_url,
            ATTR_MEDIA_CONTENT_TYPE: MEDIA_TYPE_MUSIC,
            ATTR_MEDIA_EXTRA: {
                "title": title,
                "artist": artist,
                "thumb": image,
            },
        }

        for entity_id in entity_ids:
            target = {"entity_id": entity_id}
            await hass.services.async_call(
                "media_player",
                "play_media",
                media_service_data,
                target=target,
                blocking=False,
            )

    async def handle_get_stream_url(call: ServiceCall) -> dict:
        """Service Handler: Gibt die aufgeloeste URL als Response-Data zurueck."""
        return await _resolve_from_call(call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_TIMESHIFT,
        handle_play_timeshift,
        schema=PLAY_TIMESHIFT_SCHEMA,
    )

    if SUPPORTS_RESPONSE is not None:
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_STREAM_URL,
            handle_get_stream_url,
            schema=GET_STREAM_URL_SCHEMA,
            supports_response=SUPPORTS_RESPONSE,
        )
    else:
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_STREAM_URL,
            handle_get_stream_url,
            schema=GET_STREAM_URL_SCHEMA,
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Setzt die Integration ueber configuration.yaml auf."""
    await _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setzt die Integration ueber einen UI-Config-Entry auf."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data
    await _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entfernt einen UI-Config-Entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
