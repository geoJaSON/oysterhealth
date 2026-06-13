from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from shapely.geometry import shape
from shapely.validation import explain_validity

MAX_POLYGON_VERTICES = 500


class AreaOut(BaseModel):
    id: str
    name: str
    slug: str
    region: Literal["gulf", "east_coast"]
    area_type: Literal["predefined", "custom"]
    description: str | None = None
    linked_gauges: list[str] = Field(default_factory=list)


class CustomAreaIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geojson: dict[str, Any]

    @field_validator("geojson")
    @classmethod
    def validate_polygon(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") != "Polygon":
            raise ValueError("geojson must be a GeoJSON Polygon")
        geom = shape(v)
        if not geom.is_valid:
            raise ValueError(f"Invalid geometry: {explain_validity(geom)}")
        coords = list(geom.exterior.coords)
        if len(coords) > MAX_POLYGON_VERTICES:
            raise ValueError(
                f"Polygon exceeds {MAX_POLYGON_VERTICES} vertex limit "
                f"(got {len(coords)})"
            )
        return v
