"""Konstanten fuer die ORF Sound Home Assistant Integration."""

DOMAIN = "orf_sound"

SERVICE_PLAY_TIMESHIFT = "play_timeshift"
SERVICE_GET_STREAM_URL = "get_stream_url"

ATTR_STATION = "station"
ATTR_TIME = "time"
ATTR_HOURS_AGO = "hours_ago"
ATTR_MINUTES_AGO = "minutes_ago"
ATTR_DURATION_MINUTES = "duration_minutes"

DEFAULT_USER_AGENT = "HomeAssistant-ORF-Sound/1.0"

STATIONS = {
    "oe3": {
        "name": "Hitradio Ö3",
        "channel": "oe3",
        "loopstream_host": "loopstreamoe3.apa.at",
    },
    "oe1": {
        "name": "Radio Ö1",
        "channel": "oe1",
        "loopstream_host": "loopstreamoe1.apa.at",
    },
    "fm4": {
        "name": "Radio FM4",
        "channel": "fm4",
        "loopstream_host": "loopstreamfm4.apa.at",
    },
    "wie": {
        "name": "Radio Wien",
        "channel": "oe2w",
        "loopstream_host": "loopstreamoe2w.apa.at",
    },
    "noe": {
        "name": "Radio Niederösterreich",
        "channel": "oe2n",
        "loopstream_host": "loopstreamoe2n.apa.at",
    },
    "ooe": {
        "name": "Radio Oberösterreich",
        "channel": "oe2o",
        "loopstream_host": "loopstreamoe2o.apa.at",
    },
    "sbg": {
        "name": "Radio Salzburg",
        "channel": "oe2s",
        "loopstream_host": "loopstreamoe2s.apa.at",
    },
    "ktn": {
        "name": "Radio Kärnten",
        "channel": "oe2k",
        "loopstream_host": "loopstreamoe2k.apa.at",
    },
    "stm": {
        "name": "Radio Steiermark",
        "channel": "oe2st",
        "loopstream_host": "loopstreamoe2st.apa.at",
    },
    "tir": {
        "name": "Radio Tirol",
        "channel": "oe2t",
        "loopstream_host": "loopstreamoe2t.apa.at",
    },
    "vbg": {
        "name": "Radio Vorarlberg",
        "channel": "oe2v",
        "loopstream_host": "loopstreamoe2v.apa.at",
    },
    "bgl": {
        "name": "Radio Burgenland",
        "channel": "oe2b",
        "loopstream_host": "loopstreamoe2b.apa.at",
    },
}
