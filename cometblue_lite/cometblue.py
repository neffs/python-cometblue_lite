"""
A very basic module for Eurotronic CometBlue thermostats.
They are identical to the Sygonix, Xavax Bluetooth thermostats

This version is based on the bluepy module. 
Currently only current and target temperature in manual mode is supported, nothing else.
Parts were taken from the cometblue module by im-0
"""
import logging
from datetime import timedelta
from datetime import datetime

import time
import struct

from bluepy import btle

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(10)

COMETBLUE_SERVICE = "47E9EE00-47E9-11E4-8939-164230D1DF67"
PASSWORD_HANDLE = 0x47
PASSWORD_CHAR = "47e9ee30-47e9-11e4-8939-164230d1df67"
TEMPERATURE_HANDLE = 0x3f
TEMPERATURE_CHAR = "47e9ee2b-47e9-11e4-8939-164230d1df67"
STATUS_HANDLE = 0x3d
STATUS_CHAR = "47e9ee2a-47e9-11e4-8939-164230d1df67"
DATETIME_HANDLE = 0x001d
DATETIME_CHAR = "47e9ee01-47e9-11e4-8939-164230d1df67"
MODEL_CHAR = "47e9ee2d-47e9-11e4-8939-164230d1df67"
_TEMPERATURES_STRUCT_PACKING = '<bbbbbbb'
_PIN_STRUCT_PACKING = '<I'
_STATUS_STRUCT_PACKING = '<BBB'
_DATETIME_STRUCT_PACKING = '<BBBBB'
_BATTERY_STRUCT_PACKING = '<B'
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


class CometBlue(object):
    """CometBlue Thermostat """
    def __init__(self, address, pin):
        super(CometBlue, self).__init__()
        self._address = address
        self._conn = btle.Peripheral()
        self._pin = pin
        self._manual_temp = None
        self._cur_temp = None
        self._temperature = None
        self.available = False
        self._new_status = dict()
        self._status = dict()
        self._handles = dict()
        self._model = None
#        self.update()

    def connect(self):
        """Connect to thermostat and send PIN"""
        try:
            self._conn.connect(self._address)
        except btle.BTLEException as ex:
            _LOGGER.debug("Unable to connect to the device %s, retrying: %s", self._address, ex)
            try:
                self._conn.connect(self._address)
            except Exception as ex2:
                _LOGGER.debug("Second connection try to %s failed: %s", self._address, ex2)
                raise
        if len(self._handles) <= 0:
            try:
                _LOGGER.debug("discovering characteristics %s", self._address)
                service = self._conn.getServiceByUUID(COMETBLUE_SERVICE)
                chars = service.getCharacteristics()
                self._handles = {str(a.uuid): a.getHandle() for a in chars}
            except btle.BTLEException as ex:
                _LOGGER.debug("could not discover characteristics %s: %s", self._address, ex2)
                raise
        try:
            self._conn.writeCharacteristic(self._handles[PASSWORD_CHAR], struct.pack(_PIN_STRUCT_PACKING, self._pin), withResponse=True)
        except:
            _LOGGER.debug("could not write pin %s", self._address)
            raise

    def disconnect(self):
        """Disconnect from thermostat"""
        self._conn.disconnect()
        self._conn = btle.Peripheral()

    def should_update(self):
        return self._temperature != None or len(self._new_status)>0

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
    def manual_mode(self):
        if self._status:
            return self._status['manual_mode']
        else:
            return None


    def update(self):
        """Communicate with device"""
        _LOGGER.debug("Connecting to device %s", self._address)
        try:
            self.connect()
            if not self._model:
                self._model = str(self._conn.readCharacteristic(self._handles[MODEL_CHAR]))
                
            data = self._conn.readCharacteristic(self._handles[TEMPERATURE_CHAR])
            self._cur_temp, self._manual_temp, self._target_low, self._target_high, self._offset_temp, \
                    self._window_open_detect, self._window_open_minutes = struct.unpack(
                            _TEMPERATURES_STRUCT_PACKING, data)
            data = self._conn.readCharacteristic(self._handles[STATUS_CHAR])
            self._status = CometBlue._decode_status(data)
            _LOGGER.debug("Status: %s", self._status)
            
            if self._temperature:
                _LOGGER.debug("Updating Temperature for device %s to %d", self._address, self._temperature)
                self.write_temperature()
            if len(self._new_status)>0:
                _LOGGER.debug("Updating Status for device %s", self._address)
                self.write_status()
            self.available = True
            
        except btle.BTLEGattError:
            _LOGGER.error("Can't read cometblue data (%s). Did you set the correct PIN?", self._address)
            self.available = False
            #raise
        except btle.BTLEDisconnectError:
            _LOGGER.error("Can't connect to cometblue (%s). Did you set the correct PIN?", self._address)
            self.available = False
            #raise
        finally:
            self.disconnect()
            _LOGGER.debug("Disconnected from device %s", self._address)

 
    @manual_temperature.setter
    def manual_temperature(self, temperature):
        """Set manual temperature. Call update() afterwards"""
        self._temperature = temperature
    
    @manual_mode.setter
    def manual_mode(self, mode):
        """set manual/auto mode. Call update() afterwards"""
        self._new_status['manual_mode'] = mode

    def write_temperature(self):
        self._manual_temp = int(self._temperature * 2.0)
        data = struct.pack(
                    _TEMPERATURES_STRUCT_PACKING,
                    -128, self._manual_temp,
                    -128, -128, -128, -128, -128)
        self._conn.writeCharacteristic(self._handles[TEMPERATURE_CHAR],data)
        
        self._temperature = None
    
    def write_status(self):
        status = self._status.copy()
        status.update(self._new_status)
        _LOGGER.debug("new status %s", status)
        
        data = CometBlue._encode_status(status)
        self._conn.writeCharacteristic(self._handles[STATUS_CHAR],data)
        self._status = status
        
        self._new_status = dict()

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
            if not key in _STATUS_BITMASKS:
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
