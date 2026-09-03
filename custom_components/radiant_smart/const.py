"""Constants for the Radiant Smart integration."""

DOMAIN = "radiant_smart"

CONF_HOME = "home_id"
CONF_COMPANY = "company_id"
CONF_TOKEN_DATA = "token_data"

MANUFACTURER = "Radiant Smart"

MANUFACTURERS = {
    "e4159dd6c88949ecab6feffb1c47efc1": "Radiant Smart",
}

# Topband cloud (eu) endpoints
MQTT_BROKER_HOST = "eu-tsmart-mqtt.topband-cloud.com"
MQTT_BROKER_PORT = 8883
USER_API_BASE_URL = "https://eu-tsmart-user-api.topband-cloud.com"
DEVICE_API_BASE_URL = "https://eu-tsmart-device-api.topband-cloud.com"

REQUEST_TIMEOUT = 10
