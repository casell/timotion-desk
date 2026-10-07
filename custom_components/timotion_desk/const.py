"""Constants for the TiMOTION Desk integration."""

DOMAIN = "timotion_desk"
NAME_PREFIX = "stand UP"

CONF_MIN_HEIGHT = "min_height"  # cm, empty = the desk's own limit
CONF_MAX_HEIGHT = "max_height"
CONF_IDLE_TIMEOUT = "idle_timeout"  # s connected after the last motion
CONF_CONNECTION_MODE = "connection_mode"
MODE_ON_DEMAND = "on_demand"  # connect per command, release after the idle timeout
MODE_KEEP_AWAKE = "keep_awake"  # on demand + a short connection every N minutes
MODE_ALWAYS = "always_connected"  # hold the connection, reconnect when it drops
CONNECTION_MODES = [MODE_ON_DEMAND, MODE_KEEP_AWAKE, MODE_ALWAYS]
CONF_ALWAYS_CONNECTED = "always_connected"  # bool option before 0.1.5, still honoured
# The desk sleeps about 60 min after its last activity; a connection resets that timer.
CONF_KEEP_AWAKE_INTERVAL = "keep_awake_interval"  # minutes
DEFAULT_KEEP_AWAKE_INTERVAL = 45
# Off by default: "close all covers" would otherwise move the desk with the blinds.
CONF_EXPOSE_COVER = "expose_cover"
PRESET_COUNT = 4
CONF_PRESET_NAME = "preset_{}_name"
CONF_PRESET_HEIGHT = "preset_{}_height"  # cm

DEFAULT_IDLE_TIMEOUT = 20
# Used until the desk has reported its limits.
FALLBACK_MIN_MM = 650
FALLBACK_MAX_MM = 1300
RECONNECT_DELAY = 10  # s, always-connected mode
# The desk sends ~10 frames/s while connected: this long without one means a dead link
# (typically stuck in a Bluetooth proxy), which is then closed explicitly.
STALE_TIMEOUT = 15  # s
STALE_CHECK_INTERVAL = 5  # s
# In keep-awake / always-connected mode the desk should never go silent. If it is not
# connected and not heard for this long, raise a repair issue: its Bluetooth module can
# hang (handset still works, radio silent) and only a power cycle brings it back.
UNREACHABLE_AFTER = 5 * 60  # s
