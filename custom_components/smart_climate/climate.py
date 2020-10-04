import os
import asyncio
import logging
import json
from homeassistant.components.climate import ClimateEntity
from custom_components.smartir.climate import SmartIRClimate
from custom_components.smartir import Helper
from custom_components.smartir.climate import (
    DEFAULT_NAME, CONF_UNIQUE_ID, CONF_DEVICE_CODE, CONF_CONTROLLER_DATA,
    CONF_TEMPERATURE_SENSOR, CONF_HUMIDITY_SENSOR, CONF_POWER_SENSOR,
    SUPPORT_FLAGS, PLATFORM_SCHEMA, COMPONENT_ABS_DIR
)
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL,
    HVAC_MODE_DRY, HVAC_MODE_FAN_ONLY, HVAC_MODE_AUTO,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_FAN_MODE,
    HVAC_MODES, ATTR_HVAC_MODE)
from homeassistant.const import (
    CONF_NAME, STATE_ON, STATE_UNKNOWN, ATTR_TEMPERATURE,
    PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
        hass, config, async_add_entities, discovery_info=None):
    """Set up the IR Climate platform."""
    device_code = config.get(CONF_DEVICE_CODE)
    device_files_subdir = os.path.join('codes', 'climate')
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, device_files_subdir)

    if not os.path.isdir(device_files_absdir):
        os.makedirs(device_files_absdir)

    device_json_filename = str(device_code) + '.json'
    device_json_path = os.path.join(device_files_absdir, device_json_filename)

    if not os.path.exists(device_json_path):
        _LOGGER.warning("Couldn't find the device Json file. The component will " \
                        "try to download it from the GitHub repo.")

        try:
            codes_source = ("https://raw.githubusercontent.com/"
                            "smartHomeHub/SmartIR/master/"
                            "codes/climate/{}.json")

            await Helper.downloader(codes_source.format(device_code), device_json_path)
        except Exception:
            _LOGGER.error("There was an error while downloading the device Json file. " \
                          "Please check your internet connection or if the device code " \
                          "exists on GitHub. If the problem still exists please " \
                          "place the file manually in the proper directory.")
            return

    with open(device_json_path) as j:
        try:
            device_data = json.load(j)
        except Exception:
            _LOGGER.error("The device Json file is invalid")
            return

    async_add_entities([SmartClimate(
        hass, config, device_data
    )])


class SmartClimate(SmartIRClimate):
        
    def __init__(self, hass, config, device_data):
        super().__init__(hass, config, device_data)
        self._controller_type = device_data.get('controllerType', None)
        self._temperature_control = device_data.get('temperatureControl', None)
        self._last_on_operation = self._operation_modes[1]
        self._mode_temperatures = None
        self._mode_temperatures_dict = None
        if self._temperature_control == 'Separate':
            self._init_mode_temperatures()

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if self._hvac_mode == STATE_UNKNOWN:
            self._hvac_mode = HVAC_MODE_OFF
        if last_state is not None and \
           'mode_temperatures' in last_state.attributes \
           and last_state.attributes['mode_temperatures'] is not None:
            self._init_mode_temperatures(
                last_state.attributes["mode_temperatures"])

    @property
    def device_state_attributes(self) -> dict:
        attrs = super().device_state_attributes
        if self._temperature_control == "Separate":
            attrs["mode_temperatures"] = self._mode_temperatures
        return attrs

    def _init_mode_temperatures(self, last_mode_temperatures=None):
        if self._temperature_control != "Separate":
            return
        if last_mode_temperatures is None:
            self._mode_temperatures_dict = {}
            for mode in self._operation_modes:
                self._mode_temperatures_dict[mode] = self._min_temperature
            self._mode_temperatures = json.dumps(self._mode_temperatures_dict)
        else:
            self._mode_temperatures = last_mode_temperatures
            try:
                self._mode_temperatures_dict = json.loads(
                    self._mode_temperatures)
            except Exception as ex:
                _LOGGER.error("Unable to load mode_temperatures: %s", ex)

    def update_mode_temperatures(self, operation, temperature):
        if self._temperature_control != "Separate":
            return
        self._mode_temperatures_dict[operation] = temperature
        self._mode_temperatures = json.dumps(self._mode_temperatures_dict)

    def get_mode_temperature(self, mode):
        try:
            self._mode_temperatures_dict = json.loads(self._mode_temperatures)
        except Exception as ex:
            _LOGGER.error("Unable to load mode_temperatures: %s", ex)
        return self._mode_temperatures_dict.get(mode, self._min_temperature)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperatures."""
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)  
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if self._controller_type == "Stateless" \
           and self.hvac_mode == HVAC_MODE_OFF:
            return

        if temperature is None:
            return

        if temperature < self._min_temperature or temperature > self._max_temperature:
            _LOGGER.warning('The temperature value is out of min/max range') 
            return

        current_temperature = self._target_temperature
        if self._precision == PRECISION_WHOLE:
            self._target_temperature = round(temperature)
        else:
            self._target_temperature = round(temperature, 1)

        if hvac_mode:
            await self.async_set_hvac_mode(hvac_mode)
            return

        if not self._hvac_mode.lower() == HVAC_MODE_OFF:
            self.update_mode_temperatures(
                self._last_on_operation, self._target_temperature)

            command, count = self.get_command(
                "temperature", current_temperature, self._target_temperature)
            await self.send_command(command, count)
        await self.async_update_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set operation mode."""
        if(self._controller_type == "Stateless"
           and hvac_mode != HVAC_MODE_OFF
           and self._hvac_mode == HVAC_MODE_OFF
           and "on" in self._commands):
            await self.send_command(self._commands["on"])
            if self._last_on_operation is not None:
                self._hvac_mode = self._last_on_operation
            else:
                self._hvac_mode = self._operation_modes[1]
            await asyncio.sleep(1)

        command, count = self.get_command(
            "operation", self._hvac_mode, hvac_mode)

        self._hvac_mode = hvac_mode

        if not hvac_mode == HVAC_MODE_OFF:
            self._last_on_operation = hvac_mode
            if self._mode_temperatures is not None:
                self._target_temperature = \
                    self.get_mode_temperature(hvac_mode)

        await self.send_command(command, count)
        await self.async_update_ha_state()

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode."""
        command, count = self.get_command(
            "fan", self._current_fan_mode, fan_mode)
        self._current_fan_mode = fan_mode
        if not self._hvac_mode.lower() == HVAC_MODE_OFF:
            await self.send_command(command, count)
        await self.async_update_ha_state()

    def get_command(self, mode=None, current=None, target=None) -> tuple:
        _LOGGER.info("mode:%s, current:%s, target:%s, last_on:%s"
                     % (mode, current, target, self._last_on_operation))
        command = None
        code = None
        count = 1
        if self._controller_type == "Stateless" \
           and mode is not None:
            if target == HVAC_MODE_OFF:
                code = "off"
            else:
                if mode == "temperature" and "warmer" in self._commands:
                    gap = target - current
                    code = "cooler" if gap < 0 else "warmer"
                    count = abs(int(gap/self._precision))
                else:
                    modes_list = []
                    if mode == "operation" and "operation" in self._commands:
                        modes_list = self._operation_modes
                        if HVAC_MODE_OFF == modes_list[0]:
                            modes_list = modes_list[1:]
                        code = "operation"
                    if mode == "fan" and "fan" in self._commands:
                        modes_list = self._fan_modes
                        code = "fan"
                    if modes_list:
                        current_idx = modes_list.index(current)
                        target_idx = modes_list.index(target)
                        gap = target_idx - current_idx
                        count = gap + len(modes_list) if gap < 0 else gap
        if code is not None:
            command = self._commands[code]
        _LOGGER.info("code:%s, count:%d" % (code, count))
        return (command, count)

    async def send_command(self, command=None, count=1):
        if command is None:
            super().send_command()
            return
        async with self._temp_lock:
            for i in range(count):
                try:
                    await self._controller.send(command)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    _LOGGER.exception(e)
