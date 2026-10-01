"""Constants for the TiMOTION Desk integration."""

DOMAIN = "timotion_desk"
NAME_PREFIX = "stand UP"

CONF_MIN_HEIGHT = "min_height"  # cm, empty = the desk's own limit
CONF_MAX_HEIGHT = "max_height"
CONF_IDLE_TIMEOUT = "idle_timeout"  # s connected after the last motion
CONF_ALWAYS_CONNECTED = "always_connected"
# Off by default: "close all covers" would otherwise move the desk with the blinds.
CONF_EXPOSE_COVER = "expose_cover"
PRESET_COUNT = 4
CONF_PRESET_NAME = "preset_{}_name"
CONF_PRESET_HEIGHT = "preset_{}_height"  # cm

DEFAULT_IDLE_TIMEOUT = 60
# Used until the desk has reported its limits.
FALLBACK_MIN_MM = 650
FALLBACK_MAX_MM = 1300
RECONNECT_DELAY = 10  # s, always-connected mode
