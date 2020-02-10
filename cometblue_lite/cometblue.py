"""
A very basic module for Eurotronic CometBlue thermostats.
They are identical to the Sygonix, Xavax Bluetooth thermostats

This version is based on the bluepy module. 
Currently only current and target temperature in manual mode is supported, nothing else.
Parts were taken from the cometblue module by im-0
"""
import logging
import struct

from bluepy import btle

_LOGGER = logging.getLogger(__name__)

COMETBLUE_SERVICE = "47E9EE00-47E9-11E4-8939-164230D1DF67"
PASSWORD_CHAR = "47e9ee30-47e9-11e4-8939-164230d1df67"
TEMPERATURE_CHAR = "47e9ee2b-47e9-11e4-8939-164230d1df67"
BATTERY_CHAR = "47e9ee2c-47e9-11e4-8939-164230d1df67"
STATUS_CHAR = "47e9ee2a-47e9-11e4-8939-164230d1df67"
DATETIME_CHAR = "47e9ee01-47e9-11e4-8939-164230d1df67"
MODEL_CHAR = "47e9ee2d-47e9-11e4-8939-164230d1df67"
_TEMPERATURES_STRUCT_PACKING = '<bbbbbbb'
_PIN_STRUCT_PACKING = '<I'
_STATUS_STRUCT_PACKING = '<BBB'
_DATETIME_STRUCT_PACKING = '<BBBBB'
_DAY_STRUCT_PACKING = '<BBBBBBBB'

_STATUS_BITMASKS = {
    'childlock': 0x80,
    'manual_mode': 0x1,
    'adapting': 0x400,
    'not_ready': 0x200,
    'installing': 0x400 | 0x200 | 0x100,
    'motor_moving': 0x100,
    'antifrost_activated': 0x10,
    'satisfied': 0x80000,
    'low_battery': 0x800,
    'unknown': 0x2000
}

def _decode_status(value):
    state_bytes = struct.unpack(_STATUS_STRUCT_PACKING, value)
    state_dword = struct.unpack('<I', value + b'\x00')[0]

    report = {}
    masked_out = 0
    for key, mask in _STATUS_BITMASKS.items():
        report[key] = bool(state_dword & mask == mask)
        masked_out |= mask

    report['state_as_dword'] = state_dword
    report['unused_bits'] = state_dword & ~masked_out

    return report


def _encode_status(value):
    status_dword = 0
    for key, state in value.items():
        if not state:
            continue
        if key not in _STATUS_BITMASKS:
            continue
        status_dword |= _STATUS_BITMASKS[key]
    value = struct.pack('<I', status_dword)
    # downcast to 3 bytes
    return struct.pack(_STATUS_STRUCT_PACKING, *[int(byte) for byte in value[:3]])


def _encode_datetime(dt):
    if dt.year < 2000:
        raise RuntimeError('Invalid year')
    return struct.pack(
        _DATETIME_STRUCT_PACKING,
        dt.minute,
        dt.hour,
        dt.day,
        dt.month,
        dt.year - 2000)


class CometBlue(object):
    """CometBlue Thermostat """

    def __init__(self, address, pin):
        super(CometBlue, self).__init__()
        self._address = address
        self._conn = btle.Peripheral()
        self._pin = pin
        self._manual_temp = None
        self._cur_temp = None
        self._batt_level = None
        self._temperature = None
        self.available = False
        self._new_status = dict()
        self._status = dict()
        self._handles = dict()
        self._model = None

    def connect(self):
        """Connect to thermostat and send PIN"""
        _LOGGER.debug("Connecting to device %s", self._address)

        try:
            self._conn.connect(self._address)
        except btle.BTLEException as exc:
            _LOGGER.debug("Unable to connect to the device %s, retrying.", self._address, exc_info=exc)
            try:
                self._conn.connect(self._address)
            except btle.BTLEException as exc:
                _LOGGER.debug("Second connection try to %s failed.", self._address, exc_info=exc)
                raise

        if len(self._handles) == 0:
            _LOGGER.debug("Discovering characteristics %s", self._address)
            try:
                service = self._conn.getServiceByUUID(COMETBLUE_SERVICE)
                chars = service.getCharacteristics()
                self._handles = {str(a.uuid): a.getHandle() for a in chars}
            except btle.BTLEException as exc:
                _LOGGER.debug("Could not discover characteristics %s", self._address, exc_info=exc)
                raise

        # authenticate with PIN and initialize static values
        try:
            data = struct.pack(_PIN_STRUCT_PACKING, self._pin)
            self._conn.writeCharacteristic(self._handles[PASSWORD_CHAR], data, withResponse=True)
        except btle.BTLEException as exc:
            _LOGGER.debug("Can't set PIN for device %s. Is pin=%s correct?", self._address, self._pin,
                          exc_info=exc)
            raise

    def disconnect(self):
        """Disconnect from thermostat"""
        self._conn.disconnect()
        _LOGGER.debug("Disconnected from device %s", self._address)

    def should_update(self):
        """
        Signal necessity to call update() on next cycle because values need
        to be updated or last update was unsuccessfull.
        """
        return (not self.available
                or self._temperature is not None
                or len(self._new_status) > 0)

    @property
    def manual_temperature(self):
        if self._manual_temp:
            return self._manual_temp / 2.0
        else:
            return None

    @property
    def current_temperature(self):
        if self._cur_temp:
            return self._cur_temp / 2.0
        else:
            return None

    @property
    def model(self):
        return self._model

    @property
    def battery_level(self):
        return self._batt_level

    @property
    def manual_mode(self):
        if self._status:
            return self._status['manual_mode']
        else:
            return None

    def update(self):
        """Communicate with device, first try to write new values, then read from device"""
        try:
            self.connect()

            if self._model is None:
                self._model = str(self._conn.readCharacteristic(self._handles[MODEL_CHAR]))

            if self._temperature is not None:
                self.write_temperature()

            if len(self._new_status) > 0:
                self.write_status()

            data = self._conn.readCharacteristic(self._handles[TEMPERATURE_CHAR])
            self._cur_temp, self._manual_temp, _, _, _, _, _ = struct.unpack(_TEMPERATURES_STRUCT_PACKING, data)

            data = self._conn.readCharacteristic(self._handles[STATUS_CHAR])
            decoded_status = _decode_status(data)
            if self._status != decoded_status:
                self._status = decoded_status
                _LOGGER.debug("Status: %s", self._status)

            bat_data = ord(self._conn.readCharacteristic(self._handles[BATTERY_CHAR]))
            if self._batt_level != bat_data:
                self._batt_level = bat_data
                _LOGGER.debug("Battery level: %s", self._batt_level)
        except btle.BTLEGattError as exc:
            _LOGGER.error("Can't read/write cometblue data (%s). Did you set the correct PIN?", self._address,
                          exc_info=exc)
            self.available = False
        except btle.BTLEException as exc:
            _LOGGER.error("Can't connect to cometblue (%s). Did you set the correct PIN?", self._address, exc_info=exc)
            self.available = False
        else:
            self.available = True
        finally:
            self.disconnect()

    @manual_temperature.setter
    def manual_temperature(self, temperature):
        """Set manual temperature. Call update() afterwards"""
        self._temperature = temperature

    @manual_mode.setter
    def manual_mode(self, mode):
        """set manual/auto mode. Call update() afterwards"""
        self._new_status['manual_mode'] = mode

    def write_temperature(self):
        def f_to_int(val):
            """Must be value * 2 to change and -128 otherwise"""
            return -128 if val is None else int(val * 2.0)

        _LOGGER.debug("Updating Temperatures for device {} to manual_temp={}"
                      .format(self._address, f_to_int(self._temperature)))
        data = struct.pack(
            _TEMPERATURES_STRUCT_PACKING,
            -128,  # current temp
            f_to_int(self._temperature),
            -128,  # target_temp_l
            -128,  # target_temp_h
            -128,  # offset_temp
            -128,  # window_open_detection
            -128,  # window_open_minutes
        )
        try:
            self._conn.writeCharacteristic(self._handles[TEMPERATURE_CHAR], data, withResponse=True)
        except btle.BTLEException as exc:
            _LOGGER.debug("Can't write cometblue (%s) temperature data.", self._address, exc_info=exc)
        else:
            self._temperature = None

    def write_status(self):
        status = self._status.copy()
        status.update(self._new_status)
        _LOGGER.debug("Updating Status for device {} to {}".format(self._address, status))

        data = _encode_status(status)
        try:
            self._conn.writeCharacteristic(self._handles[STATUS_CHAR], data, withResponse=True)
        except btle.BTLEException as exc:
            _LOGGER.debug("Can't write cometblue status data (%s).", self._address, exc_info=exc)
        else:
            self._new_status = dict()
