"""Constants for the ac_infinity integration."""

from bleak.exc import BleakError

DOMAIN = "ac_infinity"

BLEAK_EXCEPTIONS = (AttributeError, BleakError, TimeoutError)
