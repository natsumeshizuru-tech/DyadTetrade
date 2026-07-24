from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FactorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(min_length=1, max_length=64)
    description: str
    input_fields: tuple[str, ...]
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    code_version: str
    enabled: bool = True


class FactorRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], FactorDefinition] = {}

    def register(self, definition: FactorDefinition) -> None:
        key = (definition.name, definition.version)
        existing = self._definitions.get(key)
        if existing is not None and existing != definition:
            raise ValueError(f"factor definition already registered with different metadata: {key}")
        self._definitions[key] = definition

    def get(self, name: str, version: str | None = None) -> FactorDefinition:
        candidates = [
            definition
            for (registered_name, _), definition in self._definitions.items()
            if registered_name == name and (version is None or definition.version == version)
        ]
        if not candidates:
            raise LookupError(f"factor definition not found: {name}@{version or 'latest'}")
        return sorted(candidates, key=lambda item: item.version)[-1]

    def list(self, *, enabled_only: bool = False) -> tuple[FactorDefinition, ...]:
        definitions = sorted(self._definitions.values(), key=lambda item: (item.name, item.version))
        return tuple(item for item in definitions if item.enabled or not enabled_only)


BUILTIN_FACTOR_REGISTRY = FactorRegistry()
BUILTIN_FACTOR_REGISTRY.register(
    FactorDefinition(
        name="momentum_5d",
        version="1.0.0",
        description="Five-session close-to-close momentum using only prices visible at formation.",
        input_fields=("close",),
        parameters_schema={"lookback": {"type": "integer", "minimum": 2, "default": 5}},
        code_version="research-v1",
    )
)
BUILTIN_FACTOR_REGISTRY.register(
    FactorDefinition(
        name="price_volume_confirmation",
        version="1.0.0",
        description="Cross-sectional price momentum scaled by contemporaneous volume change.",
        input_fields=("close", "volume"),
        parameters_schema={"lookback": {"type": "integer", "minimum": 2, "default": 5}},
        code_version="research-v1",
    )
)
