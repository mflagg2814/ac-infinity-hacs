"""Device and port state."""

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace
from types import MappingProxyType
from typing import Any

from .device_information import DeviceInformation
from .routing import ProtocolProfile, resolve_profile


@dataclass(frozen=True)
class PortState:
    """One output port's state; port 0 is the controller itself."""

    id: int
    connected: bool = True
    kind: str | None = None
    level: int | None = None
    mode: int | None = None
    level_on: int | None = None
    level_off: int | None = None
    raw_type: int | None = None
    fault: bool = False
    power: bool | None = None
    raw_on_parameter: int | None = None

    def with_saved_levels(self, previous: PortState | None) -> PortState:
        """Retain settings omitted by telemetry only for the same connected load."""
        if (
            previous is not None
            and self.connected
            and previous.connected
            and (previous.kind, previous.raw_type) == (self.kind, self.raw_type)
        ):
            return replace(
                self,
                level_on=previous.level_on,
                level_off=previous.level_off,
                raw_on_parameter=previous.raw_on_parameter,
            )
        return self


class PortMap(Mapping[int, PortState]):
    """Owned, read-only port collection with dataclasses.asdict support."""

    def __init__(self, ports: Mapping[int, PortState] | None = None) -> None:
        """Copy ports, so later changes to the source don't show through."""
        self._ports = MappingProxyType(dict(ports or {}))

    def __getitem__(self, key: int) -> PortState:
        """Return the port with this id."""
        return self._ports[key]

    def __iter__(self) -> Iterator[int]:
        """Iterate over port ids."""
        return iter(self._ports)

    def __len__(self) -> int:
        """Return the number of ports."""
        return len(self._ports)

    def __deepcopy__(self, memo: dict[int, object]) -> dict[int, dict[str, Any]]:
        """Copy as plain dicts, which is what dataclasses.asdict needs."""
        return {key: asdict(value) for key, value in self._ports.items()}

    def __repr__(self) -> str:
        """Represent as a plain dict."""
        return repr(dict(self._ports))


@dataclass(frozen=True)
class DeviceInfo:
    """Controller identity and last known state."""

    type: int
    name: str
    version: int
    is_degree: bool | None = None
    tmp_state: int | None = None
    hum_state: int | None = None
    vpd_state: int | None = None
    choose_port: int | None = None
    tmp: float | None = None
    hum: float | None = None
    vpd: float | None = None
    fan_type: int | None = None
    fan_state: int | None = None
    fan: int | None = None
    work_type: int | None = None
    level_on: int | None = None
    level_off: int | None = None
    ports: Mapping[int, PortState] = field(default_factory=PortMap)
    device_information: DeviceInformation = field(default_factory=DeviceInformation)

    def __post_init__(self) -> None:
        """Own the ports as a read-only PortMap."""
        object.__setattr__(self, "ports", PortMap(self.ports))

    @property
    def profile(self) -> ProtocolProfile:
        """The protocol this model and firmware version speak."""
        return resolve_profile(self.type, self.version)

    @property
    def display_name(self) -> str:
        """Common model name with the advertised identity for disambiguation."""
        return f"{self.profile.model_name} ({self.name})"

    @property
    def outputs(self) -> tuple[PortState, ...]:
        """Known connected loads; root aggregate controls are exposed separately."""
        profile = self.profile
        if not profile.writable:
            return ()
        result = []
        for port in self.ports.values():
            if port.id and not profile.addressed:
                continue
            if port.id == 0:
                if profile.root_kind is None:
                    continue
                port = replace(port, kind=profile.root_kind)
            if port.connected and port.kind is not None:
                result.append(port)
        return tuple(sorted(result, key=lambda port: port.id))
