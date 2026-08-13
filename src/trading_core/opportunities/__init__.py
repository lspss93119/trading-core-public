"""Pure opportunity analysis over normalized market data."""

from .funding import FundingArbitrageFinder
from .spread import CrossExchangeSpreadFinder

__all__ = ["CrossExchangeSpreadFinder", "FundingArbitrageFinder"]
