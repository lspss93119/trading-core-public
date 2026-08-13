"""Explicit provider composition for exchange integrations."""

from __future__ import annotations

from .interfaces import Capability, Provider


class ProviderRegistry:
    """A deterministic registry of explicitly supplied provider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        """Register a provider once for its venue."""
        if provider.venue in self._providers:
            raise ValueError(
                f"provider already registered for venue {provider.venue!r}"
            )
        self._providers[provider.venue] = provider

    def resolve(self, venue: str) -> Provider | None:
        """Return the provider registered for a venue, if any."""
        return self._providers.get(venue)

    def require(self, venue: str) -> Provider:
        """Return a provider or raise a contextual error for an absent venue."""
        provider = self.resolve(venue)
        if provider is None:
            raise KeyError("provider is not registered for venue")
        return provider

    def providers(self) -> tuple[Provider, ...]:
        """Return providers in their explicit registration order."""
        return tuple(self._providers.values())

    def supports(self, venue: str, capability: Capability) -> bool:
        """Return whether a registered provider declares a capability."""
        provider = self.resolve(venue)
        return provider is not None and capability in provider.capabilities
