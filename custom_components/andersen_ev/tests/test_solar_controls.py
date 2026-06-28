"""Tests for Andersen EV solar control entities."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from andersen_ev.number import AndersenEvSolarMaxGridChargeNumber
from andersen_ev.switch import (
    AndersenEvSolarChargeAlwaysSwitch,
    AndersenEvSolarOverrideSwitch,
)


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    return MagicMock()


@pytest.fixture
def mock_coordinator(mock_hass):
    """Create a mock coordinator."""
    coordinator = MagicMock()
    coordinator.hass = mock_hass
    coordinator.data = []
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_device():
    """Create a mock device with solar methods."""
    device = MagicMock()
    device.device_id = "test_device_123"
    device.friendly_name = "Test Charger"
    device.user_lock = False
    device.last_status = {
        "sysProductName": "Andersen A2",
        "sysHwVersion": "1.0",
    }
    device._last_solar_status = {
        "solarOverride": False,
        "solarChargeAlways": False,
        "solarMaxGridChargePercent": 50,
    }
    device.last_solar_status = device._last_solar_status
    device.set_solar = AsyncMock(return_value=True)
    device.model_name = "Andersen A2"
    return device


class TestAndersenEvSolarOverrideSwitch:
    """Test AndersenEvSolarOverrideSwitch entity."""

    @pytest.mark.asyncio
    async def test_solar_override_switch_is_on(self, mock_coordinator, mock_device):
        """Test solar override switch reads on state correctly."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarOverride"] = True

        switch = AndersenEvSolarOverrideSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        assert switch.is_on is True

    @pytest.mark.asyncio
    async def test_solar_override_switch_is_off(self, mock_coordinator, mock_device):
        """Test solar override switch reads off state correctly."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarOverride"] = False

        switch = AndersenEvSolarOverrideSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_solar_override_switch_turn_on(self, mock_coordinator, mock_device):
        """Test turning on solar override switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvSolarOverrideSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_on()

        mock_device.set_solar.assert_called_once_with(override=True)
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_solar_override_switch_turn_off(self, mock_coordinator, mock_device):
        """Test turning off solar override switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvSolarOverrideSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_off()

        mock_device.set_solar.assert_called_once_with(override=False)
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_solar_override_switch_unique_id(self, mock_coordinator, mock_device):
        """Test solar override switch has correct unique ID."""
        switch = AndersenEvSolarOverrideSwitch(mock_coordinator, mock_device)
        assert switch.unique_id == "test_device_123_solar_solarOverride"


class TestAndersenEvSolarChargeAlwaysSwitch:
    """Test AndersenEvSolarChargeAlwaysSwitch entity."""

    @pytest.mark.asyncio
    async def test_solar_charge_always_switch_turn_on(self, mock_coordinator, mock_device):
        """Test turning on solar charge always switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_on()

        mock_device.set_solar.assert_called_once_with(charge_always=True)

    @pytest.mark.asyncio
    async def test_solar_charge_always_switch_turn_off(self, mock_coordinator, mock_device):
        """Test turning off solar charge always switch."""
        mock_coordinator.data = [mock_device]

        switch = AndersenEvSolarChargeAlwaysSwitch(mock_coordinator, mock_device)
        switch.hass = mock_coordinator.hass
        switch.async_write_ha_state = MagicMock()
        await switch.async_turn_off()

        mock_device.set_solar.assert_called_once_with(charge_always=False)


class TestAndersenEvSolarMaxGridChargeNumber:
    """Test AndersenEvSolarMaxGridChargeNumber entity."""

    @pytest.mark.asyncio
    async def test_solar_max_grid_charge_number_value(self, mock_coordinator, mock_device):
        """Test solar max grid charge number reads value correctly."""
        mock_coordinator.data = [mock_device]
        mock_device._last_solar_status["solarMaxGridChargePercent"] = 75

        number = AndersenEvSolarMaxGridChargeNumber(mock_coordinator, mock_device)
        number.hass = mock_coordinator.hass
        assert number.native_value == 75.0

    @pytest.mark.asyncio
    async def test_solar_max_grid_charge_number_set_value(self, mock_coordinator, mock_device):
        """Test setting solar max grid charge number value."""
        mock_coordinator.data = [mock_device]

        number = AndersenEvSolarMaxGridChargeNumber(mock_coordinator, mock_device)
        number.hass = mock_coordinator.hass
        number.async_write_ha_state = MagicMock()
        await number.async_set_native_value(80.0)

        mock_device.set_solar.assert_called_once_with(max_grid_charge_percent=80)
        mock_coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_solar_max_grid_charge_number_min_max(self, mock_coordinator, mock_device):
        """Test solar max grid charge number min and max values."""
        number = AndersenEvSolarMaxGridChargeNumber(mock_coordinator, mock_device)
        assert number.min_value == 0.0
        assert number.max_value == 100.0

    @pytest.mark.asyncio
    async def test_solar_max_grid_charge_number_unique_id(self, mock_coordinator, mock_device):
        """Test solar max grid charge number has correct unique ID."""
        number = AndersenEvSolarMaxGridChargeNumber(mock_coordinator, mock_device)
        assert number.unique_id == "test_device_123_solar_max_grid_charge_percent"

    @pytest.mark.asyncio
    async def test_solar_max_grid_charge_number_unit(self, mock_coordinator, mock_device):
        """Test solar max grid charge number has correct unit."""
        number = AndersenEvSolarMaxGridChargeNumber(mock_coordinator, mock_device)
        assert number._attr_unit_of_measurement == "%"
