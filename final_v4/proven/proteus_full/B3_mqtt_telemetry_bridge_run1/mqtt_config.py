"""mqtt_config -- configuration for the telemetry bridge."""
from dataclasses import dataclass


@dataclass
class BridgeConfig:
    host: str = "localhost"
    port: int = 1883
    base_topic: str = "plant/telemetry"
    qos: int = 1
    client_id: str = "bridge-01"

    @staticmethod
    def from_dict(d: dict) -> "BridgeConfig":
        fields = BridgeConfig.__dataclass_fields__
        return BridgeConfig(**{k: v for k, v in d.items() if k in fields})
