"""Abstract base class for all data providers."""

from abc import ABC, abstractmethod


class DataProvider(ABC):
    """Interface that every market-data provider must implement."""

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for *symbol*.

        Returns a dict with at least: price, change_pct, change_abs, timestamp.
        """

    @abstractmethod
    async def get_history(self, symbol: str, period: str) -> list[dict]:
        """Fetch historical OHLCV bars for *symbol* over *period*.

        *period* is one of: '1D', '1W', '1M', 'YTD'.
        Returns a list of dicts with: date, open, high, low, close, volume.
        """

    @abstractmethod
    async def search(self, query: str) -> list[dict]:
        """Search for instruments matching *query*.

        Returns a list of dicts with: symbol, name, type, exchange.
        """
