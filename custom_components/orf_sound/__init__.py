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
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("device_id"): vol.Any(cv.string, [cv.string]),
        vol.Optional("area_id"): vol.Any(cv.string, [cv.string]),
        vol.Optional("target"): dict,
        vol.Required(ATTR_STATION): vol.In(list(STATIONS.keys())),
        vol.Optional(ATTR_TIME): cv.string,
        vol.Optional(ATTR_HOURS_AGO): vol.Coerce(float),
        vol.Optional(ATTR_MINUTES_AGO): vol.Coerce(float),
        vol.Optional(ATTR_DURATION_MINUTES): cv.positive_int,
    },
    extra=vol.ALLOW_EXTRA,
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


def _extract_media_player_entities(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Ermittelt alle Media Player Entitaets-IDs aus dem ServiceCall (Entity, Device, Area, Target)."""
    entity_ids: set[str] = set()

    # 1. HA Core Target Resolution (falls verfuegbar)
    try:
        from homeassistant.helpers.service import async_extract_referenced_entity_ids
        selected = async_extract_referenced_entity_ids(hass, call)
        if selected:
            entity_ids.update(selected.referenced | selected.indirectly_referenced)
    except Exception as err:
        _LOGGER.debug("Core target extraction nicht verfuegbar oder Fehler: %s", err)

    # 2. Direkte Entitaeten aus call.data und call.data.get("target")
    data = call.data or {}
    target_dict = data.get("target") if isinstance(data.get("target"), dict) else {}

    for src in (data, target_dict):
        e_val = src.get("entity_id")
        if isinstance(e_val, str):
            entity_ids.add(e_val)
        elif isinstance(e_val, (list, tuple, set)):
            entity_ids.update(e_val)

    # 3. device_id und area_id aufloesen (ueber Entity Registry)
    device_ids: set[str] = set()
    area_ids: set[str] = set()
    for src in (data, target_dict):
        d_val = src.get("device_id")
        if isinstance(d_val, str):
            device_ids.add(d_val)
        elif isinstance(d_val, (list, tuple, set)):
            device_ids.update(d_val)

        a_val = src.get("area_id")
        if isinstance(a_val, str):
            area_ids.add(a_val)
        elif isinstance(a_val, (list, tuple, set)):
            area_ids.update(a_val)

    if device_ids or area_ids:
        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(hass)

            for dev_id in device_ids:
                entries = er.async_entries_for_device(ent_reg, dev_id)
                for entry in entries:
                    if entry.domain == "media_player":
                        entity_ids.add(entry.entity_id)

            for a_id in area_ids:
                entries = er.async_entries_for_area(ent_reg, a_id)
                for entry in entries:
                    if entry.domain == "media_player":
                        entity_ids.add(entry.entity_id)
        except Exception as reg_err:
            _LOGGER.debug("Registry Lookup fehlgeschlagen: %s", reg_err)

    # 4. Nur Media Player Entitaeten behalten
    media_players = [eid for eid in entity_ids if eid.startswith("media_player.")]
    if not media_players and entity_ids:
        media_players = list(entity_ids)

    return sorted(list(set(media_players)))


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
        entity_ids = _extract_media_player_entities(hass, call)
        if not entity_ids:
            raise ValueError(
                "Kein Ziel-Player (entity_id oder device_id) angegeben. "
                "Bitte waehle im Dienstaufruf unter 'Ziel' (target) einen Media Player aus."
            )

        stream_data = await _resolve_from_call(call)
        stream_url = stream_data["stream_url"]
        title = stream_data.get("display_title", stream_data.get("title", stream_data["station_name"]))
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

            # Pruefen, ob es sich um einen Music Assistant Player handelt
            is_mass = entity_id.startswith("media_player.mass_") or "mass" in entity_id
            if not is_mass:
                try:
                    from homeassistant.helpers import entity_registry as er
                    ent_reg = er.async_get(hass)
                    entry = ent_reg.async_get(entity_id)
                    if entry and entry.platform in ("mass", "music_assistant"):
                        is_mass = True
                except Exception:
                    pass

            mass_domain = None
            if hass.services.has_service("music_assistant", "play_media"):
                mass_domain = "music_assistant"
            elif hass.services.has_service("mass", "play_media"):
                mass_domain = "mass"

            if is_mass and mass_domain:
                _LOGGER.info("Uebergebe Stream an Music Assistant (%s) fuer %s: %s", mass_domain, entity_id, title)
                await hass.services.async_call(
                    mass_domain,
                    "play_media",
                    {
                        "media_id": stream_url,
                        "media_type": "url",
                    },
                    target=target,
                    blocking=False,
                )
            else:
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
