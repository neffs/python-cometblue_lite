"""
A very basic module for Eurotronic CometBlue thermostats.
They are identical to the Sygonix, Xavax Bluetooth thermostats

This version is based on the bluepy module.
Currently only current and target temperature in manual mode is supported, nothing else.
Parts were taken from the cometblue module by im-0
"""
import logging
import struct
import time
from contextlib import contextmanager

from bluepy import btle

_LOGGER = logging.getLogger(__name__)

PASSWORD_CHAR = "47e9ee30-47e9-11e4-8939-164230d1df67"
TEMPERATURE_CHAR = "47e9ee2b-47e9-11e4-8939-164230d1df67"
BATTERY_CHAR = "47e9ee2c-47e9-11e4-8939-164230d1df67"
STATUS_CHAR = "47e9ee2a-47e9-11e4-8939-164230d1df67"
DATETIME_CHAR = "47e9ee01-47e9-11e4-8939-164230d1df67"
SOFTWARE_REV = "00002a28-0000-1000-8000-00805f9b34fb"       # software_revision (0.0.6-sygonix1)
MODEL_CHAR = "00002a24-0000-1000-8000-00805f9b34fb"         # model_number (Comet Blue)
MANUFACTURER_CHAR = "00002a29-0000-1000-8000-00805f9b34fb"  # manufacturer_name (EUROtronic GmbH)
FIRMWARE_CHAR = "47e9ee2d-47e9-11e4-8939-164230d1df67"      # firmware_revision2 (COBL0126)
_PIN_STRUCT_PACKING = '<I'
_DATETIME_STRUCT_PACKING = '<BBBBB'
_DAY_STRUCT_PACKING = '<BBBBBBBB'


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


class CometBlueStates:
    """CometBlue Thermostat States"""
    TEMPERATURE_OFF = 7.5   # special temperature, valve fully closed
    _TEMPERATURES_STRUCT_PACKING = '<bbbbbbb'
    _STATUS_STRUCT_PACKING = '<BBB'

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

    def __init__(self):
        self.is_off = False
        self.firmware_rev = None
        self.manufacturer = None
        self.model = None
        self.name = None
        self.software_rev = None
        self._status = dict()
        self._battery_level = None
        self._current_temp = None
        self.clear_temperatures()

    def clear_temperatures(self):
        self.target_temperature = None
        self.target_temp_l = None
        self.target_temp_h = None
        self.offset_temperature = None
        self.window_open_detection = None
        self.window_open_minutes = None

    @property
    def battery_level(self):
        return self._battery_level

    @battery_level.setter
    def battery_level(self, value):
        self._battery_level = ord(value)

    @property
    def locked(self):
        return self._status.get('childlock', False)

    @locked.setter
    def locked(self, value):
        self._status['childlock'] = value

    @property
    def manual_mode(self):
        return self._status.get('manual_mode', False)

    @manual_mode.setter
    def manual_mode(self, value):
        self._status['manual_mode'] = value

    @property
    def low_battery(self):
        return self._status.get('low_battery', False)

    @low_battery.setter
    def low_battery(self, value):
        self._status['low_battery'] = value

    @property
    def status(self):
        actions = ['adapting', 'not_ready', 'installing', 'motor_moving', 'unknown', 'satisfied']
        active_actions = [k for k, v in self._status.items() if v is True and k in actions]

        return active_actions.pop(0) if active_actions else 'unknown'

    @property
    def temperature(self):
        """Current temperature, adjusted by offset temperature"""
        if self._current_temp is not None:
            offset_temp = getattr(self, 'offset_temperature', 0.0)
            return self._current_temp + offset_temp

    @property
    def window_open(self):
        return self._status.get('antifrost_activated', False)

    @window_open.setter
    def window_open(self, value):
        self._status['antifrost_activated'] = value

    @property
    def status_code(self):
        def encode_status(value):
            status_dword = 0
            for key, state in value.items():
                if not state:
                    continue
                if key not in CometBlueStates._STATUS_BITMASKS:
                    continue
                status_dword |= CometBlueStates._STATUS_BITMASKS[key]
            value = struct.pack('<I', status_dword)
            # downcast to 3 bytes
            return struct.pack(CometBlueStates._STATUS_STRUCT_PACKING, *[int(byte) for byte in value[:3]])

        # check for changed status_code with 'is not None'
        if len(self._status) == 0:
            return None
        else:
            _LOGGER.debug("Updating Status to %s", self._status)
            return encode_status(self._status)

    @status_code.setter
    def status_code(self, val):
        def decode_status(value):
            state_bytes = struct.unpack(CometBlueStates._STATUS_STRUCT_PACKING, value)
            state_dword = struct.unpack('<I', value + b'\x00')[0]

            report = {}
            masked_out = 0
            for key, mask in CometBlueStates._STATUS_BITMASKS.items():
                report[key] = bool(state_dword & mask == mask)
                masked_out |= mask

            report['state_as_dword'] = state_dword
            report['unused_bits'] = state_dword & ~masked_out

            return report

        if val is None:
            self._status = dict()
        else:
            self._status = decode_status(val)

    @property
    def temperatures(self):
        def float_to_int(value):
            """Encode float for CometBlue update, returns value * 2.0, if value is set, else -128"""
            return -128 if value is None else int(value * 2.0)

        def int_to_int(value):
            """Encode float for CometBlue update, returns value, if value is set, else -128"""
            return -128 if value is None else int(value)

        temps = {
            'current_temp': -128,  # current temp
            'manual_temp': float_to_int(self.target_temperature),
            'target_temp_l': float_to_int(self.target_temp_l),
            'target_temp_h': float_to_int(self.target_temp_h),
            'offset_temp': float_to_int(self.offset_temperature),
            'window_open_detection': int_to_int(self.window_open_detection),
            'window_open_minutes': int_to_int(self.window_open_minutes),
        }
        _LOGGER.debug("Updating Temperatures to {}".format(temps))

        data = struct.pack(
            CometBlueStates._TEMPERATURES_STRUCT_PACKING,
            *temps.values(),
        )
        return data

    @temperatures.setter
    def temperatures(self, value):
        temps = struct.unpack(CometBlueStates._TEMPERATURES_STRUCT_PACKING, value)
        current_temp, manual_temp, target_low, target_high, offset_temp, window_open_detect, window_open_minutes = temps

        # abort on any invalid temperature value
        if -128 in temps:
            _LOGGER.debug("Got invalid Temperatures: {}".format(temps))
            return

        # preserve current "target_temperature" when TEMPERATURE_OFF is active
        if manual_temp / 2.0 == CometBlueStates.TEMPERATURE_OFF:
            self.is_off = True
        else:
            self.is_off = False
            self.target_temperature = manual_temp / 2.0

        self._current_temp = current_temp / 2.0
        self.target_temp_l = target_low / 2.0
        self.target_temp_h = target_high / 2.0
        self.offset_temperature = offset_temp / 2.0
        self.window_open_detection = window_open_detect
        self.window_open_minutes = window_open_minutes

        _LOGGER.debug("Got Temperatures: {}".format(temps))

    @property
    def all_temperatures_none(self):
        """True if any of the temperature properties is not None"""
        values = set((self.target_temperature, self.target_temp_l, self.target_temp_h, self.offset_temperature, self.window_open_detection, self.window_open_minutes))
        values.remove(None)
        return len(values) == 0



class CometBlue:
    """CometBlue Thermostat """

    def __init__(self, address, pin):
        super(CometBlue, self).__init__()
        self._address = address
        self._pin = pin
        self.available = False
        self._handles = dict()
        self._current = CometBlueStates()
        self._target = CometBlueStates()
        # btle.Debugging = True

    @contextmanager
    def btle_connection(self):
        """Contextmanager to handle a bluetooth connection to Comet Blue device.
        Any problem that arises when using the connection, will be handled,
        the connection closed and any resources aquired released.
        Debug logging for analysis is integrated, the errors are raised to be
        handled by the user.

        The following aspects are managed:
        * Connect handles setup, preparations for read/write and authentication.
        * Read/Write error handling.
        * Disconnect handles proper releasing of any aquired resources.
        """
        conn = self._connect()
        self.available = True
        try:
            yield conn
        except btle.BTLEException as ex:
            _LOGGER.debug("Couldn't read/write cometblue data for device %s:\n%s", self._address, ex)
            self.available = False
            raise
        except BrokenPipeError as ex:
            _LOGGER.debug("Device %s BrokenPipeError: %s", self._address, ex)
            self.available = False
            raise btle.BTLEDisconnectError() from ex
        finally:
            self._disconnect(conn)

    def _connect(self):
        """Connect to thermostat and send PIN"""
        conn = btle.Peripheral()
        _LOGGER.debug("Connecting to device %s", self._address)
        try:
            conn.connect(self._address)
        except btle.BTLEException as ex:
            _LOGGER.debug("Couldn't establish connection with %s, retrying:\n%s", self._address, ex)
            time.sleep(2)
            try:
                conn.connect(self._address)
            except Exception as ex:
                _LOGGER.debug("Connecting to device %s failed:\n%s", self._address, ex)
                raise

        if len(self._handles) == 0:
            _LOGGER.debug("Discovering characteristics for device %s", self._address)
            try:
                chars = conn.getCharacteristics()
                self._handles = {str(a.uuid): a.getHandle() for a in chars}
            except btle.BTLEException as ex:
                _LOGGER.debug("Couldn't discover characteristics: %s", ex)
                raise

        # authenticate with PIN and initialize static values
        try:
            data = struct.pack(_PIN_STRUCT_PACKING, self._pin)
            conn.writeCharacteristic(self._handles[PASSWORD_CHAR], data, withResponse=True)
        except btle.BTLEException:
            _LOGGER.debug("Provided pin=%d was not accepted by device %s", self._pin, self._address)
            raise

        _LOGGER.debug("Connected and authenticated with device %s", self._address)
        return conn

    def _disconnect(self, connection):
        """Disconnect from thermostat"""
        try:
            connection.disconnect()
        except (btle.BTLEException, BrokenPipeError) as ex:
            _LOGGER.debug("Couldn't disconnect from device %s:\n%s", self._address, ex)
            raise btle.BTLEDisconnectError() from ex
        else:
            _LOGGER.debug("Disconnected from device %s", self._address)

    def should_update(self):
        """
        Signal necessity to call update() on next cycle because values need
        to be updated or last update was unsuccessfull.
        """
        return (not self.available
                or self._target.target_temperature is not None
                or self._target.offset_temperature is not None
                or self._target.status_code is not None)

    @property
    def firmware_rev(self):
        """Return firmware revision (e.g. COBL0126)"""
        return self._current.firmware_rev

    @property
    def is_off(self):
        return self._current.is_off

    @is_off.setter
    def is_off(self, value):
        """Set device in special 'off' mode.
            value: True to set 'Mode: manual, Target temperature: 7.5', False to set last
                known target temperature, else to high temp., Mode is not changed.
        """
        if value:
            self._target.manual_mode = True
            self._target.target_temperature = CometBlueStates.TEMPERATURE_OFF
        else:
            if self._current.target_temperature is not None:
                self._target.target_temperature = self._current.target_temperature
            else:
                self._target.target_temperature = self._current.target_temp_h

    @property
    def locked(self):
        """Return True if device is in child lock"""
        return self._current.locked

    @property
    def low_battery(self):
        """Return True if device is signalling log battery"""
        return self._current.low_battery

    @property
    def manufacturer(self):
        """Return manufacturer name (e.g. EUROtronic GmbH)"""
        return self._current.manufacturer

    @property
    def model(self):
        """Return model (e.g. Comet Blue)"""
        return self._current.model

    @property
    def software_rev(self):
        """Return software revision (e.g. 0.0.6-sygonix1)"""
        return self._current.software_rev

    @property
    def status(self):
        """Return current device status
        one of: adapting, not_ready, installing, motor_moving, unknown, satisfied"""
        return self._current.status

    @property
    def target_temperature(self):
        return self._current.target_temperature

    @target_temperature.setter
    def target_temperature(self, temperature):
        """Set manual temperature. Call update() afterwards"""
        if self.is_off:
            self._current.target_temperature = temperature
        else:
            self._target.target_temperature = temperature

    @property
    def target_temperature_low(self):
        return self._current.target_temp_l

    @target_temperature_low.setter
    def target_temperature_low(self, temperature):
        self._target.target_temp_l = temperature

    @property
    def target_temperature_high(self):
        return self._current.target_temp_h

    @target_temperature_high.setter
    def target_temperature_high(self, temperature):
        self._target.target_temp_h = temperature

    @property
    def current_temperature(self):
        return self._current.temperature

    @property
    def offset_temperature(self):
        return self._current.offset_temperature

    @offset_temperature.setter
    def offset_temperature(self, temperature):
        """Set offset temperature. Call update() afterwards"""
        self._target.offset_temperature = temperature

    @property
    def battery_level(self):
        return self._current.battery_level

    @property
    def manual_mode(self):
        return self._current.manual_mode

    @manual_mode.setter
    def manual_mode(self, mode):
        """set manual/auto mode. Call update() afterwards"""
        self._target.manual_mode = mode

    @property
    def window_open(self):
        """Return True if device detected opened window"""
        return self._current.window_open

    def update(self):
        """Communicate with device, first try to write new values, then read from device"""
        current = self._current
        target = self._target

        with self.btle_connection() as conn:
            device_infos = [
                current.model,
                current.firmware_rev,
                current.manufacturer,
                current.software_rev
            ]
            if None in device_infos:
                _LOGGER.debug("Fetching hardware information for device %s", self._address)
                current.model = conn.readCharacteristic(self._handles[MODEL_CHAR]).decode()
                current.firmware_rev = conn.readCharacteristic(self._handles[FIRMWARE_CHAR]).decode()
                current.manufacturer = conn.readCharacteristic(self._handles[MANUFACTURER_CHAR]).decode()
                current.software_rev = conn.readCharacteristic(self._handles[SOFTWARE_REV]).decode()
                _LOGGER.debug("Sucessfully fetched hardware information")

            if not target.all_temperatures_none:
                conn.writeCharacteristic(self._handles[TEMPERATURE_CHAR],
                                         target.temperatures,
                                         withResponse=True)
                target.clear_temperatures()
                _LOGGER.debug("Successfully updated Temperatures for device %s", self._address)

            if target.status_code is not None:
                conn.writeCharacteristic(self._handles[STATUS_CHAR],
                                         target.status_code,
                                         withResponse=True)
                target.status_code = None
                _LOGGER.debug("Successfully updated status for device %s", self._address)

            current.temperatures = conn.readCharacteristic(self._handles[TEMPERATURE_CHAR])
            current.status_code = conn.readCharacteristic(self._handles[STATUS_CHAR])
            current.battery_level = conn.readCharacteristic(self._handles[BATTERY_CHAR])
            _LOGGER.debug("Successfully fetched new readings for device %s", self._address)
