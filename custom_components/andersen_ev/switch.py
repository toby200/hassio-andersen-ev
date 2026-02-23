"""Switch platform for Andersen EV charging schedules."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any

import requests
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AndersenEvCoordinator
from .const import DOMAIN
from .konnect import const
from .konnect.bearerauth import BearerAuth

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Andersen EV schedule switches."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for device in coordinator.data:
        # Get device info including schedule names and schedule slots
        device_info = await device.get_device_info()
        if not device_info or "deviceInfo" not in device_info:
            _LOGGER.warning("Could not retrieve device info for %s", device.friendly_name)
            continue

        # Get schedule slots from device_info
        if "deviceStatus" not in device_info or "scheduleSlotsArray" not in device_info["deviceStatus"]:
            _LOGGER.warning("Could not retrieve schedule slots for %s", device.friendly_name)
            continue

        schedule_slots = device_info["deviceStatus"]["scheduleSlotsArray"]
        device_info_data = device_info["deviceInfo"]

        # Create switches for each schedule
        for idx, _slot in enumerate(schedule_slots):
            # Get the schedule name from deviceInfo if available
            schedule_name_key = f"schedule{idx}Name"
            if device_info_data.get(schedule_name_key):
                schedule_name = device_info_data[schedule_name_key]
            else:
                schedule_name = f"Schedule {idx + 1}"

            entities.append(AndersenEvScheduleSwitch(coordinator, device, idx, schedule_name))

    async_add_entities(entities)


class AndersenEvScheduleSwitch(CoordinatorEntity, SwitchEntity):  # pylint: disable=abstract-method
    """Representation of an Andersen EV charging schedule switch."""

    def __init__(
        self,
        coordinator: AndersenEvCoordinator,
        device,
        index: int,
        schedule_name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device = device
        self._schedule_index = index
        self._schedule_name = schedule_name
        # Use standardized naming format: "Friendly Name Schedule X"
        self._attr_name = f"{device.friendly_name} Schedule {index + 1}"
        self._attr_unique_id = f"{device.device_id}_schedule_{index}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_id)},
            "name": f"{device.friendly_name} ({device.device_id})",
            "manufacturer": "Andersen EV",
            "model": "A2",
        }
        self._attr_icon = "mdi:calendar-clock"
        self._update_model_from_device_status()

    @property
    def extra_state_attributes(self):
        """Return additional attributes for the entity."""
        return {
            "schedule_name": self._schedule_name,
            "schedule_index": self._schedule_index,
        }

    def _update_model_from_device_status(self):
        """Update model information from device status if available."""
        # First try to use the model name from the API if available
        if hasattr(self._device, "model_name") and self._device.model_name:
            self._attr_device_info["model"] = self._device.model_name
        # Fall back to the information from device status
        elif self._device.last_status:
            status = self._device.last_status
            if "sysProductName" in status:
                self._attr_device_info["model"] = status["sysProductName"]
            elif "sysProductId" in status:
                self._attr_device_info["model"] = status["sysProductId"]
            elif "sysHwVersion" in status:
                self._attr_device_info["model"] = f"A2 (HW: {status['sysHwVersion']})"

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        # Check for the device in the coordinator data
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                return self.coordinator.last_update_success
        return False

    @property
    def is_on(self) -> bool:
        """Return true if the schedule is enabled."""
        # Check if device exists in coordinator data and update reference
        for device in self.coordinator.data:
            if device.device_id == self._device.device_id:
                self._device = device
                break

        # Try to get the latest scheduleSlotsArray from the device's last status
        # This ensures we pick up changes made in the mobile app
        if self._device.last_status:
            status = self._device.last_status
            if "scheduleSlotsArray" in status and len(status["scheduleSlotsArray"]) > self._schedule_index:
                schedule_slot = status["scheduleSlotsArray"][self._schedule_index]
                return schedule_slot["enabled"]

        # If we can't get the state from the last status, return False as a safe default
        _LOGGER.debug(
            "Could not determine state for schedule %s of %s", self._schedule_index, self._device.friendly_name
        )
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule."""
        await self._set_schedule_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule."""
        await self._set_schedule_enabled(False)

    async def _set_schedule_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the schedule."""
        try:
            # Get the current schedule slots from the device's last status
            if not self._device.last_status or "scheduleSlotsArray" not in self._device.last_status:
                # If we don't have the data in the coordinator, fetch it
                device_info = await self._device.get_device_info()
                if (
                    not device_info
                    or "deviceStatus" not in device_info
                    or "scheduleSlotsArray" not in device_info["deviceStatus"]
                ):
                    _LOGGER.warning(
                        "Failed to get schedule slots for %s",
                        self._device.friendly_name,
                    )
                    return
                schedule_slots = copy.deepcopy(device_info["deviceStatus"]["scheduleSlotsArray"])
            else:
                # Use the data from the coordinator
                schedule_slots = copy.deepcopy(self._device.last_status["scheduleSlotsArray"])

            # Modify the enabled state of the specified schedule
            if len(schedule_slots) > self._schedule_index:
                # Update the enabled state
                schedule_slots[self._schedule_index]["enabled"] = enabled

                # Create the proper format for the API with sch0, sch1, etc. keys
                schedule_to_update = schedule_slots[self._schedule_index]
                formatted_slots = {f"sch{self._schedule_index}": schedule_to_update}

                # Send the properly formatted schedule slots to the API
                success = await self._send_set_schedules_mutation(formatted_slots, enabled)

                if success:
                    # Update the local state immediately
                    if self._device.last_status:
                        if "scheduleSlotsArray" not in self._device.last_status:
                            self._device.last_status["scheduleSlotsArray"] = schedule_slots
                        elif len(self._device.last_status["scheduleSlotsArray"]) <= self._schedule_index:
                            # Extend the array if needed
                            while len(self._device.last_status["scheduleSlotsArray"]) <= self._schedule_index:
                                self._device.last_status["scheduleSlotsArray"].append({})

                        # Update the enabled state
                        self._device.last_status["scheduleSlotsArray"][self._schedule_index]["enabled"] = enabled

                    # Force the entity to update its state immediately
                    self.async_write_ha_state()

                    # Request a refresh of the coordinator data to update all entities
                    await self.coordinator.async_request_refresh()
                else:
                    _LOGGER.warning(
                        "Failed to update schedule state for %s Schedule %s",
                        self._device.friendly_name,
                        self._schedule_index + 1,
                    )
            else:
                _LOGGER.warning("Schedule index %s out of range", self._schedule_index)

        except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.error("Error setting schedule state: %s", err)

    async def _send_set_schedules_mutation(self, schedule_slots, enabled=None) -> bool:
        """Send the setSchedules mutation to the Andersen EV API."""
        # Ensure we have a valid token
        try:
            await self._device.api.ensure_valid_auth()

            url = const.GRAPHQL_URL
            query = """
mutation setSchedules($deviceId: ID!, $scheduleSlots: ScheduleSlotsInput!) {
  setSchedules(deviceId: $deviceId, scheduleSlots: $scheduleSlots) {
    id
    name
    return_value
  }
}
"""

            variables = {
                "deviceId": self._device.device_id,
                "scheduleSlots": schedule_slots,
            }

            body = {
                "operationName": "setSchedules",
                "variables": variables,
                "query": query,
            }

            # Add debug logging to see what we're sending
            _LOGGER.debug("Sending schedule update for device %s, payload: %s", self._device.friendly_name, variables)

            try:
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: requests.post(url, json=body, auth=BearerAuth(self._device.api.token), timeout=30),
                )

                if response.status_code == 401:
                    # Token expired, refresh and retry
                    _LOGGER.debug("Authentication token expired during schedule update, refreshing")
                    await self._device.api.refresh_token()
                    return await self._send_set_schedules_mutation(schedule_slots, enabled)

                if response.status_code != 200:
                    _LOGGER.warning("Failed to update schedule, status code: %s", response.status_code)
                    return False

                # Parse response and check for errors
                try:
                    response_body = response.json()
                    # Log the full response for analysis
                    _LOGGER.debug("API Response JSON: %s", json.dumps(response_body))
                except Exception as json_err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                    _LOGGER.error("Error parsing JSON response: %s", json_err)
                    return False

                if not response_body:
                    _LOGGER.error("Empty response received from API")
                    return False

                if "errors" in response_body:
                    _LOGGER.warning("GraphQL errors in response: %s", response_body["errors"])
                    return False

                # Consider it successful if data present
                # and no errors, even if setSchedules is null
                if "data" in response_body and "errors" not in response_body:
                    state_text = "enabled" if enabled else "disabled" if enabled is not None else "updated"
                    _LOGGER.info("Schedule %s for %s %s", self._schedule_name, self._device.friendly_name, state_text)
                    return True

                # Assume success based on status code 200
                _LOGGER.debug(
                    "No clear success/failure indicator in response, assuming success based on status code 200"
                )
                return True

            except requests.RequestException as req_err:
                _LOGGER.error("Request error updating schedule: %s", req_err)
                return False

            except Exception as err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _LOGGER.error("Error updating schedule: %s", err)
                return False
        except Exception as outer_err:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            _LOGGER.error("Authentication error in schedule update: %s", outer_err)
            return False
