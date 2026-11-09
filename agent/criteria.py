from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RentalCriteria:
    city: str = "Berlin"
    districts: list[str] = field(default_factory=list)
    min_rooms: float | None = None
    max_rooms: float | None = None
    min_size_sqm: float | None = None
    max_size_sqm: float | None = None
    min_rent: float | None = None
    max_rent: float | None = None  # warm rent (Warmmiete) in EUR
    max_cold_rent: float | None = None  # cold rent (Kaltmiete) in EUR
    furnished: bool | None = None
    balcony: bool | None = None
    pets_allowed: bool | None = None
    move_in_date: str | None = None  # YYYY-MM-DD
    wg_suitable: bool | None = None  # suitable for shared flat
    keywords: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"City: {self.city}"]
        if self.districts:
            parts.append(f"Districts: {', '.join(self.districts)}")
        if self.min_rooms or self.max_rooms:
            r = f"{self.min_rooms or '?'}-{self.max_rooms or '?'} rooms"
            parts.append(r)
        if self.min_size_sqm or self.max_size_sqm:
            s = f"{self.min_size_sqm or '?'}-{self.max_size_sqm or '?'} m²"
            parts.append(s)
        if self.max_rent:
            parts.append(f"Max warm rent: {self.max_rent} EUR")
        if self.max_cold_rent:
            parts.append(f"Max cold rent: {self.max_cold_rent} EUR")
        if self.furnished is not None:
            parts.append(f"Furnished: {'Yes' if self.furnished else 'No'}")
        if self.balcony:
            parts.append("Balcony required")
        if self.pets_allowed:
            parts.append("Pets allowed required")
        if self.move_in_date:
            parts.append(f"Move-in: {self.move_in_date}")
        if self.keywords:
            parts.append(f"Keywords: {', '.join(self.keywords)}")
        return "\n".join(parts)
