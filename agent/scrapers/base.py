from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from agent.criteria import RentalCriteria


@dataclass
class Listing:
    title: str
    url: str
    source: str
    price_warm: float | None = None
    price_cold: float | None = None
    rooms: float | None = None
    size_sqm: float | None = None
    address: str | None = None
    district: str | None = None
    description: str | None = None
    contact_email: str | None = None
    contact_name: str | None = None
    contact_form_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    available_from: str | None = None
    features: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"**{self.title}**"]
        if self.price_warm:
            parts.append(f"Warm rent: {self.price_warm} EUR")
        if self.price_cold:
            parts.append(f"Cold rent: {self.price_cold} EUR")
        if self.rooms:
            parts.append(f"Rooms: {self.rooms}")
        if self.size_sqm:
            parts.append(f"Size: {self.size_sqm} m²")
        if self.address:
            parts.append(f"Address: {self.address}")
        elif self.district:
            parts.append(f"District: {self.district}")
        if self.available_from:
            parts.append(f"Available from: {self.available_from}")
        parts.append(f"Link: {self.url}")
        return "\n".join(parts)


class BaseScraper(ABC):
    name: str = "base"

    @abstractmethod
    async def search(self, criteria: RentalCriteria) -> list[Listing]:
        pass

    @abstractmethod
    async def get_listing_details(self, url: str) -> Listing | None:
        pass
