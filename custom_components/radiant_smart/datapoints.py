"""Declarative mapping of Topband data points to Home Assistant entities."""

from __future__ import annotations

from typing import cast

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate import HVACMode
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)

from .models import (
    BinarySensorSpec,
    BoilerFunction,
    BoilerMode,
    ButtonSpec,
    ClimateSpec,
    DataPoint,
    NumberSpec,
    SelectSpec,
    SensorSpec,
    SmartDevice,
    SwitchSpec,
    WaterHeaterSpec,
)

# Data points that are never exposed as entities
IGNORED_POINTS = frozenset(
    {
        "PARAM_ID_SYS_DEVICE_MODULE",
        "PARAM_ID_TH_DELETE_TH_ADDR",
    }
)

# Data point name prefixes that are never exposed as entities
# (weekly programs are complex binary blobs, not yet supported)
IGNORED_PREFIXES = (
    "PARAM_ID_BOILER_DHW_PRO_",
    "PARAM_ID_TH_ROOM_PROG_DAY_",
)

TEMPERATURE_UNIT = UnitOfTemperature.CELSIUS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ot_slave_status_options() -> dict[int, str]:
    """Build the readable names for the OT slave status bitfield."""
    status = BoilerFunction
    return {
        status.IDLE: "Standby",
        status.UNKNOWN_1: "Unknown (1)",
        status.RADIATOR: "Radiators",
        status.DOMESTIC: "Domestic Water",
        status.FLAME: "Flame",
        status.RADIATOR | status.FLAME: "Flame + Radiators",
        status.DOMESTIC | status.FLAME: "Flame + Domestic Water",
        status.UNKNOWN_1
        | status.RADIATOR
        | status.DOMESTIC
        | status.FLAME: "Unknown (15)",
        status.UNKNOWN_1
        | status.RADIATOR
        | status.DOMESTIC
        | status.FLAME
        | status.UNKNOWN_5
        | status.UNKNOWN_6: "Unknown (63)",
        status.UNKNOWN_1
        | status.RADIATOR
        | status.DOMESTIC
        | status.FLAME
        | status.UNKNOWN_5
        | status.UNKNOWN_6
        | status.UNKNOWN_7
        | status.UNKNOWN_8: "Unknown (255)",
    }


# ---------------------------------------------------------------------------
# Water heaters
# ---------------------------------------------------------------------------

WATER_HEATERS = (
    WaterHeaterSpec(
        key="heating",
        name="Radiators Water Heater",
        icon="mdi:radiator",
        required_keys=(
            "PARAM_ID_BOILER_CH_CUR_TEMP",
            "PARAM_ID_BOILER_CH_SET_RANGE_DOWN",
            "PARAM_ID_BOILER_CH_SET_RANGE_UP",
            "PARAM_ID_BOILER_CH_MAX_SETPOINT",
            "SYS_WORK_MODE",
        ),
        min_temp_key="PARAM_ID_BOILER_CH_SET_RANGE_DOWN",
        max_temp_key="PARAM_ID_BOILER_CH_SET_RANGE_UP",
        current_temp_key="PARAM_ID_BOILER_CH_CUR_TEMP",
        target_temp_key="PARAM_ID_BOILER_CH_MAX_SETPOINT",
        work_mode_key="SYS_WORK_MODE",
        work_type=BoilerMode.HEATING,
    ),
    WaterHeaterSpec(
        key="sanitary",
        name="Sanitary Water Heater",
        icon="mdi:faucet",
        required_keys=(
            "PARAM_ID_BOILER_DHW_CUR_TEMP",
            "PARAM_ID_BOILER_DHW_SET_RANGE_DOWN",
            "PARAM_ID_BOILER_DHW_SET_RANGE_UP",
            "PARAM_ID_BOILER_DHW_TRG_TEMP",
            "SYS_WORK_MODE",
        ),
        min_temp_key="PARAM_ID_BOILER_DHW_SET_RANGE_DOWN",
        max_temp_key="PARAM_ID_BOILER_DHW_SET_RANGE_UP",
        current_temp_key="PARAM_ID_BOILER_DHW_CUR_TEMP",
        target_temp_key="PARAM_ID_BOILER_DHW_TRG_TEMP",
        work_mode_key="SYS_WORK_MODE",
        work_type=BoilerMode.SANITARY,
    ),
)

# ---------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------

CLIMATES = (
    ClimateSpec(
        key="thermostat",
        name="Thermostat",
        icon="mdi:thermostat",
        required_keys=(
            "PARAM_ID_TH_CUR_ROOM_TEMP",
            "PARAM_ID_TH_TRG_ROOM_TEMP",
            "PARAM_ID_TH_OVERRIDE_ENABLE",
            "PARAM_ID_TH_OVERRIDE_TEMP",
            "PARAM_ID_TH_WORK_MODE",
            "PARAM_ID_TH_CH_STATUS",
        ),
        current_temp_key="PARAM_ID_TH_CUR_ROOM_TEMP",
        target_temp_key="PARAM_ID_TH_TRG_ROOM_TEMP",
        override_enable_key="PARAM_ID_TH_OVERRIDE_ENABLE",
        override_temp_key="PARAM_ID_TH_OVERRIDE_TEMP",
        hvac_mode_key="PARAM_ID_TH_WORK_MODE",
        current_action_key="PARAM_ID_TH_CH_STATUS",
        hvac_modes={0: HVACMode.AUTO, 1: HVACMode.HEAT, 4: HVACMode.OFF},
    ),
)

# ---------------------------------------------------------------------------
# Selects, switches, numbers
# ---------------------------------------------------------------------------

SELECTS = {
    "SYS_WORK_MODE": SelectSpec(
        key="work_mode",
        name="Work Mode",
        icon="mdi:auto-mode",
        options={
            BoilerMode.STANDBY: "Standby",
            BoilerMode.SANITARY: "Sanitary",
            BoilerMode.HEATING: "Heating",
            BoilerMode.HEATING_SANITARY: "Heating & Sanitary",
        },
    ),
    "PARAM_ID_TH_CH_CALCULATE": SelectSpec(
        key="control_type",
        name="Control Type",
        icon="mdi:auto-mode",
        options={
            0: "ON/OFF",
            1: "Outdoor Temperature",
            2: "Room Temperature",
            3: "Room & Outdoor Temperature",
            4: "None",
        },
    ),
}

SWITCHES = {
    "PARAM_ID_BOILER_DHW_PRO_EN": SwitchSpec(
        key="dhw_program",
        name="Domestic Water Heating Program",
        icon="mdi:home-clock",
        device_class=SwitchDeviceClass.SWITCH,
    ),
    "PARAM_ID_SYS_DST_ENABLE": SwitchSpec(
        key="auto_time_sync",
        name="Auto Time Sync",
        icon="mdi:clock",
        device_class=SwitchDeviceClass.SWITCH,
    ),
}

NUMBERS = {
    "PARAM_ID_TH_POWEROFF_FROZE_TEMP": NumberSpec(
        key="frost_temperature",
        name="Frost Temperature",
        icon="mdi:snowflake-thermometer",
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        unit=UnitOfTemperature.CELSIUS,
        min_value=5.0,
        max_value=15.0,
        step=0.5,
    ),
    "PARAM_ID_TH_CLIMATE_CURVE": NumberSpec(
        key="climate_curve",
        name="Climate Curve",
        icon="mdi:chart-bell-curve-cumulative",
        mode=NumberMode.BOX,
        # The device reports this as a plain integer (t=1); the observed
        # value of 54 did not fit the previous 0-30 range.
        min_value=0.0,
        max_value=100.0,
        step=1.0,
    ),
}

# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

RESET_FAULT_KEY = "RESET_FAULT"

BUTTONS = {
    RESET_FAULT_KEY: ButtonSpec(
        key="reset_fault",
        name="Reset Fault",
        icon="mdi:alert-remove",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_value=1,
    ),
}

# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------

# Exact data point name -> sensor spec
SENSOR_SPECS: dict[str, SensorSpec] = {
    "PARAM_ID_BOILER_CH_TRG_TEMP": SensorSpec(
        key="heating_target_temperature",
        name="Heating Target Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_OUTDOOR_TEMP": SensorSpec(
        key="outdoor_temperature",
        name="Outdoor Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_CH_CUR_TEMP": SensorSpec(
        key="boiler_ch_temperature",
        name="Boiler CH Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_TH_ANTI_FROZE_TEMP": SensorSpec(
        key="anti_frost_temperature",
        name="Anti Frost Temperature",
        icon="mdi:snowflake-thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_TH_CFT_TEMP": SensorSpec(
        key="cft_temperature",
        name="CFT Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_TH_ECO_TEMP": SensorSpec(
        key="eco_temperature",
        name="Eco Temperature",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_PWM_OUT": SensorSpec(
        key="power",
        name="Power",
        icon="mdi:gas-burner",
        unit=PERCENTAGE,
        suggested_display_precision=0,
    ),
    "PARAM_ID_SYS_SOFT_VER": SensorSpec(
        key="software_version",
        name="Software Version",
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_OT_SLAVE_STATUS": SensorSpec(
        key="boiler_status",
        name="Status",
        icon="mdi:state-machine",
        device_class=SensorDeviceClass.ENUM,
        options=_ot_slave_status_options(),
    ),
}

SENSOR_SUFFIX_SPECS: tuple[tuple[tuple[str, ...], SensorSpec], ...] = (
    (
        ("_TEMP",),
        SensorSpec(
            key="temperature",
            name="Temperature",
            icon="mdi:thermometer",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTemperature.CELSIUS,
        ),
    ),
    (
        ("_RATE",),
        SensorSpec(
            key="flow_rate",
            name="Flow Rate",
            icon="mdi:waves",
            device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfVolumeFlowRate.LITERS_PER_SECOND,
        ),
    ),
    (
        ("_PRESSURE",),
        SensorSpec(
            key="pressure",
            name="Pressure",
            icon="mdi:gauge",
            device_class=SensorDeviceClass.PRESSURE,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfPressure.BAR,
        ),
    ),
    (
        ("WIFI_SIGNAL", "WIFI__SIGNAL"),
        SensorSpec(
            key="wifi_signal",
            name="WiFi Signal",
            icon="mdi:signal-variant",
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    ),
    (
        ("_FAULT_CODE",),
        SensorSpec(
            key="fault_code",
            name="Fault Code",
            icon="mdi:alert",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Binary sensors
# ---------------------------------------------------------------------------

# The OT slave status feeds multiple binary sensors and is also exposed as an
# enum sensor, therefore it is never consumed (popped) by the binary sensors.
OT_SLAVE_STATUS_KEY = "PARAM_ID_BOILER_OT_SLAVE_STATUS"

OT_SLAVE_STATUS_BINARY_SPECS = (
    BinarySensorSpec(
        key="flame",
        name="Flame",
        icon="mdi:fire",
        device_class=BinarySensorDeviceClass.RUNNING,
        bit_mask=BoilerFunction.FLAME,
    ),
    BinarySensorSpec(
        key="radiators_water_flow",
        name="Radiators Water Flow",
        icon="mdi:radiator",
        device_class=BinarySensorDeviceClass.OPENING,
        bit_mask=BoilerFunction.RADIATOR,
    ),
    BinarySensorSpec(
        key="domestic_water_flow",
        name="Domestic Water Flow",
        icon="mdi:faucet",
        device_class=BinarySensorDeviceClass.OPENING,
        bit_mask=BoilerFunction.DOMESTIC,
    ),
)

BINARY_SENSOR_SPECS: dict[str, BinarySensorSpec] = {
    "PARAM_ID_BOILER_IS_ROOM_SENSOR_ENABL": BinarySensorSpec(
        key="external_temperature_sensor",
        name="External Temperature Sensor",
        icon="mdi:thermometer-probe",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_IS_OT_CONNECTED": BinarySensorSpec(
        key="thermostat_connection",
        name="Thermostat Connection",
        icon="mdi:thermostat-box",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "PARAM_ID_BOILER_OTC_ENABLE": BinarySensorSpec(
        key="outdoor_temperature_compensation",
        name="Outdoor Temperature Compensation",
    ),
}

BINARY_SENSOR_SUFFIX_SPECS: tuple[tuple[tuple[str, ...], BinarySensorSpec], ...] = (
    (
        ("_RELAY_STATE",),
        BinarySensorSpec(
            key="relay_state",
            name="Relay State",
            icon="mdi:electric-switch",
            device_class=BinarySensorDeviceClass.POWER,
            options={1: 0, 2: 1},
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    ),
    (
        ("_ENABLE", "_ENABL", "_CONNECTED"),
        BinarySensorSpec(
            key="enabled",
            name="Enabled",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

# All data point names that are explicitly mapped to an entity somewhere.
# Prefix filtering (IGNORED_PREFIXES) must not swallow these, e.g. the
# domestic water heating program enable flag shares its prefix with the
# unsupported weekly program points.
_KNOWN_POINTS = frozenset(
    set(SELECTS)
    | set(SWITCHES)
    | set(NUMBERS)
    | set(BUTTONS)
    | set(SENSOR_SPECS)
    | set(BINARY_SENSOR_SPECS)
    | {key for spec in WATER_HEATERS for key in spec.required_keys}
    | {key for spec in CLIMATES for key in spec.required_keys}
)


def _expose_point(name: str) -> bool:
    """Return whether a data point may be exposed as an entity."""
    if name in IGNORED_POINTS:
        return False
    # Only drop unmapped points with an ignored prefix; explicitly mapped
    # points sharing the prefix (e.g. the DHW program enable flag) survive.
    return not name.startswith(IGNORED_PREFIXES) or name in _KNOWN_POINTS


def build_entity_configs(device: SmartDevice) -> None:
    """Map the device data points to entity configurations.

    Populates the entity config lists on the device. Consumed data points
    are removed from the working set; leftovers end up as unknown_points
    (exposed as diagnostic sensors).
    """
    data_points = {
        name: dp for name, dp in device.data_points.items() if _expose_point(name)
    }

    device.water_heaters = _build_water_heaters(data_points)
    device.climates = _build_climates(data_points)
    device.selects = _build_selects(data_points)
    device.switches = _build_switches(data_points)
    device.numbers = _build_numbers(data_points)
    device.buttons = _build_buttons(data_points)
    device.binary_sensors = _build_binary_sensors(data_points)
    device.sensors = _build_sensors(data_points)

    device.unknown_points = data_points


def _build_water_heaters(
    data_points: dict[str, DataPoint],
) -> list[tuple[WaterHeaterSpec, dict[str, DataPoint]]]:
    """Build water heater configurations."""
    configs: list[tuple[WaterHeaterSpec, dict[str, DataPoint]]] = []
    for spec in WATER_HEATERS:
        if not all(key in data_points for key in spec.required_keys):
            continue
        points = {
            "min_temp": data_points.pop(spec.min_temp_key),
            "max_temp": data_points.pop(spec.max_temp_key),
            "current_temp": data_points.pop(spec.current_temp_key),
            "target_temp": data_points.pop(spec.target_temp_key),
            # SYS_WORK_MODE is shared with the water heater select entity
            "work_mode": cast(DataPoint, data_points.get(spec.work_mode_key)),
        }
        configs.append((spec, points))
    return configs


def _build_climates(
    data_points: dict[str, DataPoint],
) -> list[tuple[ClimateSpec, dict[str, DataPoint]]]:
    """Build climate configurations."""
    configs: list[tuple[ClimateSpec, dict[str, DataPoint]]] = []
    for spec in CLIMATES:
        if not all(key in data_points for key in spec.required_keys):
            continue
        points = {
            "current_temp": data_points.pop(spec.current_temp_key),
            "target_temp": data_points.pop(spec.target_temp_key),
            "override_enable": data_points.pop(spec.override_enable_key),
            "override_temp": data_points.pop(spec.override_temp_key),
            "hvac_mode": data_points.pop(spec.hvac_mode_key),
            "current_action": data_points.pop(spec.current_action_key),
        }
        configs.append((spec, points))
    return configs


def _build_selects(
    data_points: dict[str, DataPoint],
) -> list[tuple[SelectSpec, DataPoint]]:
    """Build select configurations."""
    configs: list[tuple[SelectSpec, DataPoint]] = []
    for key, spec in SELECTS.items():
        if key in data_points:
            configs.append((spec, data_points.pop(key)))
    return configs


def _build_switches(
    data_points: dict[str, DataPoint],
) -> list[tuple[SwitchSpec, DataPoint]]:
    """Build switch configurations."""
    configs: list[tuple[SwitchSpec, DataPoint]] = []
    for key, spec in SWITCHES.items():
        if key in data_points:
            configs.append((spec, data_points.pop(key)))
    return configs


def _build_numbers(
    data_points: dict[str, DataPoint],
) -> list[tuple[NumberSpec, DataPoint]]:
    """Build number configurations."""
    configs: list[tuple[NumberSpec, DataPoint]] = []
    for key, spec in NUMBERS.items():
        if key in data_points:
            configs.append((spec, data_points.pop(key)))
    return configs


def _build_buttons(
    data_points: dict[str, DataPoint],
) -> list[tuple[ButtonSpec, DataPoint]]:
    """Build button configurations."""
    configs: list[tuple[ButtonSpec, DataPoint]] = []
    for key, spec in BUTTONS.items():
        if key in data_points:
            configs.append((spec, data_points.pop(key)))
    return configs


def _build_binary_sensors(
    data_points: dict[str, DataPoint],
) -> list[tuple[BinarySensorSpec, DataPoint]]:
    """Build binary sensor configurations."""
    configs: list[tuple[BinarySensorSpec, DataPoint]] = []

    if OT_SLAVE_STATUS_KEY in data_points:
        status_point = data_points[OT_SLAVE_STATUS_KEY]
        for spec in OT_SLAVE_STATUS_BINARY_SPECS:
            configs.append((spec, status_point))

    for key, point in list(data_points.items()):
        spec = BINARY_SENSOR_SPECS.get(key)
        if spec is not None:
            del data_points[key]
            configs.append((spec, point))
            continue
        for suffixes, suffix_spec in BINARY_SENSOR_SUFFIX_SPECS:
            if key.endswith(suffixes):
                del data_points[key]
                configs.append((suffix_spec, point))
                break

    return configs


def _build_sensors(
    data_points: dict[str, DataPoint],
) -> list[tuple[SensorSpec, DataPoint]]:
    """Build sensor configurations."""
    configs: list[tuple[SensorSpec, DataPoint]] = []

    for key, point in list(data_points.items()):
        spec = SENSOR_SPECS.get(key)
        if spec is not None:
            del data_points[key]
            configs.append((spec, point))
            continue
        for suffixes, suffix_spec in SENSOR_SUFFIX_SPECS:
            if key.endswith(suffixes):
                del data_points[key]
                configs.append((suffix_spec, point))
                break

    return configs
