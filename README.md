# Radiant Smart Home Assistant integration

Custom component for controlling **Radiant Smart** boilers (Topband cloud based)
with Home Assistant.

Connects to the Topband cloud over REST (login, device discovery) and receives
real-time device state and sends commands over MQTT.

## Installation

### HACS (recommended)

1. Add this repository to HACS as a custom repository (category: *Integration*).
2. Install **Radiant Smart**.
3. Restart Home Assistant.

### Manual

1. Copy the `custom_components/radiant_smart` folder into the
   `custom_components` folder of your Home Assistant installation.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration** and search for
   **Radiant Smart**.
2. Select the brand, then enter the **email** and **password** of your
   Radiant Smart app account.
3. Select the **home** that contains your devices.

The integration registers one Home Assistant device per cloud device. All
state updates arrive as push messages over MQTT, no polling involved.

## Tested devices

| Device  | Role                          | Status |
| ------- | ----------------------------- | ------ |
| TH311   | Boiler controller / gateway   | ✅      |
| TH623   | Room thermostat               | ✅      |

## Entities

Depending on the capabilities reported by each device, the integration creates:

- **Water heaters** — *Radiators Water Heater* (CH circuit) and *Sanitary Water
  Heater* (DHW circuit), with target temperature, on/off and operation mode.
  Turning off one circuit while the boiler runs in combined mode switches to
  the other circuit instead of switching the boiler off.
- **Climate** — *Thermostat*, with room temperature, target temperature and
  auto/heat/off modes. Setting a temperature in auto mode activates the
  temporary override temperature on the device.
- **Select** — *Work Mode* (standby / sanitary / heating / heating & sanitary)
  and *Control Type* (ON/OFF, outdoor temp, room temp, ...).
- **Switch** — *Domestic Water Heating Program*, *Auto Time Sync*.
- **Number** — *Frost Temperature*, *Climate Curve*.
- **Button** — *Reset Fault*, to clear an active boiler fault.
- **Sensors** — outdoor temperature, boiler CH temperature, heating target
  temperature, flow rate, water pressure, PWM power, fault code, WiFi signal,
  software version, boiler status (enum of the OpenTherm slave status
  bitfield), plus diagnostic sensors for any unmapped data point.
- **Binary sensors** — flame, radiators water flow, domestic water flow,
  thermostat connection, external temperature sensor, relay state.

## Protocol (reverse engineering notes)

The integration speaks to the Topband cloud (EU region). The protocol summary
below documents how it works; raw captures live in a separate
`radiant-smart` repository.

### REST API

- Login: `POST https://eu-tsmart-user-api.topband-cloud.com/tsmart-user-api/appLogin/v2/login`
  with `userName`, `password` (MD5 hash) and `companyId`. The response carries
  a JWT `token` used as the `authorization` header of all subsequent calls.
- Device list: `POST .../tsmart-device-api/family/v2/device/list` with
  `familyId`. Each entry contains device metadata and the full
  `pointDataMap` of current data point values.

### MQTT

- Broker: `eu-tsmart-mqtt.topband-cloud.com:8883` (TLS).
- Authentication: MQTT username `app/<JWT>`, password `<JWT>`.
- Topics (per gateway): `<gatewayProductId>/<gatewayUid>/download/point/data`
  for commands, `<gatewayProductId>/<gatewayUid>/upload/point/data` for
  status updates, plus `<productId>/<gatewayUid>/business`.

Command payload (cmd `99`):

```json
{
  "common": {
    "productId": "<gateway product id>",
    "serial": 123,
    "timestamp": 1769032788504,
    "uid": "<gateway uid>"
  },
  "data": [
    {
      "cmd": 99,
      "command": [{ "i": 8270, "t": 1, "len": 0, "v": 10 }],
      "mac": "<device mac>"
    }
  ],
  "method": "command"
}
```

Status payload (cmd `98`) echoes the new data point values:

```json
{
  "method": "command",
  "data": [
    {
      "cmd": 98,
      "mac": "<device mac>",
      "command": { "SYS_WORK_MODE": { "i": 8270, "t": 1, "v": 10 } }
    }
  ]
}
```

### Data points

Each data point has an index (`i`), a type (`t`) and a value (`v`):

| Type | Meaning                                |
| ---- | -------------------------------------- |
| 1    | int, enum or bool                      |
| 2    | float, raw value ×10 (e.g. `182` → 18.2 °C) |
| 7    | constant value                         |
| 8    | raw hex string (versions, programs)    |

Notable points:

- `SYS_WORK_MODE` — boiler mode: `0` standby, `2` sanitary, `3` heating,
  `10` heating & sanitary.
- `PARAM_ID_BOILER_OT_SLAVE_STATUS` — OpenTherm slave status bitfield:
  bit 0 unknown, bit 1 radiators, bit 2 domestic water, bit 3 flame.

## Debugging

Enable debug logging for the integration:

```yaml
logger:
  logs:
    custom_components.radiant_smart: debug
```
