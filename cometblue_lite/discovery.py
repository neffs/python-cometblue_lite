"""Discover switchbot devices."""

from __future__ import annotations

import asyncio
import logging

import bleak
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from cometblue import *

_LOGGER = logging.getLogger(__name__)
CONNECT_LOCK = asyncio.Lock()

DEFAULT_SCAN_TIMEOUT = 5
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_TIMEOUT = 1

class GetSwitchbotDevices:
    """Scan for all Switchbot devices and return by type."""

    def __init__(self, interface: int = 0) -> None:
        """Get switchbot devices class constructor."""
        self._interface = f"hci{interface}"
        self._adv_data: dict[str, AdvertisementData] = {}

    def detection_callback(
        self,
        device: BLEDevice,
        advertisement_data: AdvertisementData,
    ) -> None:
        """Callback for device detection."""
        discovery = advertisement_data #parse_advertisement_data(device, advertisement_data)
        #print(discovery.local_name)
        if discovery and device.name == "Comet Blue":
            print(device.metadata)
            print(device.address)
            print(discovery.service_data)
            print(discovery.service_uuids)
            print(discovery.manufacturer_data)

            #self._adv_data[discovery.address] = discovery

    async def discover(
        self, retry: int = DEFAULT_RETRY_COUNT, scan_timeout: int = DEFAULT_SCAN_TIMEOUT
    ) -> dict:
        """Find switchbot devices and their advertisement data."""

        devices = None
        devices = bleak.BleakScanner(
            # TODO: Find new UUIDs to filter on. For example, see
            # https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/4ad138bb09f0fbbfa41b152ca327a78c1d0b6ba9/devicetypes/meter.md
            adapter=self._interface,
        )
        devices.register_detection_callback(self.detection_callback)

        async with CONNECT_LOCK:
            await devices.start()
            await asyncio.sleep(scan_timeout)
            await devices.stop()

        if devices is None:
            if retry < 1:
                _LOGGER.error(
                    "Scanning for Switchbot devices failed. Stop trying", exc_info=True
                )
                return self._adv_data

            _LOGGER.warning(
                "Error scanning for Switchbot devices. Retrying (remaining: %d)",
                retry,
            )
            await asyncio.sleep(DEFAULT_RETRY_TIMEOUT)
            return await self.discover(retry - 1, scan_timeout)

        return self._adv_data

    async def _get_devices_by_model(
        self,
        model: str,
    ) -> dict:
        """Get switchbot devices by type."""
        if not self._adv_data:
            await self.discover()

        return {
            address: adv
            for address, adv in self._adv_data.items()
            if adv.data.get("model") == model
        }


    async def get_device_data(
        self, address: str
    ) -> dict[str, AdvertisementData] | None:
        """Return data for specific device."""
        if not self._adv_data:
            await self.discover()

        return {
            device: adv
            for device, adv in self._adv_data.items()
            # MacOS uses UUIDs instead of MAC addresses
            if adv.data.get("address") == address
        }
