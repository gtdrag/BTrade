"""
Data Providers - Abstraction layer for market data sources.

Supports multiple data sources with automatic fallback:
1. E*TRADE Production (real-time, requires approved API keys)
2. Alpaca (real-time, free API keys)
3. Finnhub (real-time with slight delay, free tier)
4. Yahoo Finance (15-min delay, no auth needed - fallback only)
"""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)


class DataSource(Enum):
    """Available data sources."""

    ETRADE = "etrade"
    ALPACA = "alpaca"
    FINNHUB = "finnhub"
    YAHOO = "yahoo"  # Fallback only - 15min delay


@dataclass
class Quote:
    """Standardized quote data."""

    symbol: str
    current_price: float
    open_price: float
    high_price: float
    low_price: float
    bid: float
    ask: float
    volume: int
    source: DataSource
    is_realtime: bool


class DataProvider(ABC):
    """Abstract base class for data providers."""

    source: DataSource
    is_realtime: bool = False

    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get current quote for a symbol."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and available."""
        pass


class ETradeProvider(DataProvider):
    """E*TRADE data provider (real-time with production API)."""

    source = DataSource.ETRADE
    is_realtime = True

    def __init__(self, client=None):
        """
        Initialize with an ETradeClient instance.

        Args:
            client: Authenticated ETradeClient (production, not sandbox)
        """
        self.client = client

    def is_available(self) -> bool:
        return self.client is not None and self.client.is_authenticated()

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_available():
            return None

        try:
            quote_data = self.client.get_quote(symbol)
            all_data = quote_data.get("All", {})

            return Quote(
                symbol=symbol,
                current_price=float(all_data.get("lastTrade", 0)),
                open_price=float(all_data.get("open", 0)),
                high_price=float(all_data.get("high", 0)),
                low_price=float(all_data.get("low", 0)),
                bid=float(all_data.get("bid", 0)),
                ask=float(all_data.get("ask", 0)),
                volume=int(all_data.get("totalVolume", 0)),
                source=self.source,
                is_realtime=True,
            )
        except Exception as e:
            logger.warning(f"E*TRADE quote failed for {symbol}: {e}")
            return None


class AlpacaProvider(DataProvider):
    """Alpaca data provider (real-time, free API)."""

    source = DataSource.ALPACA
    is_realtime = True

    BASE_URL = "https://data.alpaca.markets/v2"

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        """
        Initialize with Alpaca API credentials.

        Args:
            api_key: Alpaca API key (or set ALPACA_API_KEY env var)
            secret_key: Alpaca secret key (or set ALPACA_SECRET_KEY env var)
        """
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_available():
            return None

        try:
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
            }

            # Use snapshot endpoint - gets all data in one call
            response = requests.get(
                f"{self.BASE_URL}/stocks/{symbol}/snapshot",
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            # Extract from snapshot
            daily_bar = data.get("dailyBar", {})
            latest_quote = data.get("latestQuote", {})
            latest_trade = data.get("latestTrade", {})

            return Quote(
                symbol=symbol,
                current_price=float(latest_trade.get("p", 0)),
                open_price=float(daily_bar.get("o", 0)),
                high_price=float(daily_bar.get("h", 0)),
                low_price=float(daily_bar.get("l", 0)),
                bid=float(latest_quote.get("bp", 0)),
                ask=float(latest_quote.get("ap", 0)),
                volume=int(daily_bar.get("v", 0)),
                source=self.source,
                is_realtime=True,
            )
        except Exception as e:
            logger.warning(f"Alpaca quote failed for {symbol}: {e}")
            return None

    def get_crypto_quote(self, symbol: str = "BTC/USD") -> Optional[Quote]:
        """Get real-time crypto quote from Alpaca."""
        if not self.is_available():
            return None

        try:
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
            }

            # Crypto uses different endpoint
            response = requests.get(
                "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes",
                headers=headers,
                params={"symbols": symbol},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            quote_data = data.get("quotes", {}).get(symbol, {})
            if not quote_data:
                return None

            # Crypto quotes have bid/ask but no OHLC in latest quote
            mid_price = (quote_data.get("bp", 0) + quote_data.get("ap", 0)) / 2

            return Quote(
                symbol=symbol,
                current_price=mid_price,
                open_price=mid_price,  # Not available in latest quote
                high_price=mid_price,
                low_price=mid_price,
                bid=float(quote_data.get("bp", 0)),
                ask=float(quote_data.get("ap", 0)),
                volume=0,
                source=self.source,
                is_realtime=True,
            )
        except Exception as e:
            logger.warning(f"Alpaca crypto quote failed for {symbol}: {e}")
            return None

    def get_crypto_bars(
        self, symbol: str, start_date: str, end_date: str, timeframe: str = "1Day"
    ) -> Optional[list]:
        """Get historical crypto bars from Alpaca."""
        if not self.is_available():
            return None

        try:
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
            }

            response = requests.get(
                "https://data.alpaca.markets/v1beta3/crypto/us/bars",
                headers=headers,
                params={
                    "symbols": symbol,
                    "timeframe": timeframe,
                    "start": start_date,
                    "end": end_date,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            return data.get("bars", {}).get(symbol, [])
        except Exception as e:
            logger.warning(f"Alpaca crypto bars failed for {symbol}: {e}")
            return None

    def get_historical_bars(
        self, symbol: str, start_date: str, end_date: str, timeframe: str = "1Day"
    ) -> Optional[list]:
        """
        Get historical OHLCV bars from Alpaca.

        Args:
            symbol: Stock symbol (e.g., "IBIT") or crypto (e.g., "BTC/USD")
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            timeframe: Bar timeframe (1Min, 5Min, 15Min, 1Hour, 1Day)

        Returns:
            List of bar dicts with keys: t, o, h, l, c, v
        """
        # Check if it's a crypto symbol
        if "/" in symbol:
            return self.get_crypto_bars(symbol, start_date, end_date, timeframe)
        if not self.is_available():
            return None

        try:
            headers = {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret_key,
            }

            all_bars = []
            next_page_token = None

            while True:
                params = {
                    "timeframe": timeframe,
                    "start": start_date,
                    "end": end_date,
                    "limit": 10000,
                    "feed": "iex",  # Required for free tier (SIP data needs paid subscription)
                }
                if next_page_token:
                    params["page_token"] = next_page_token

                response = requests.get(
                    f"{self.BASE_URL}/stocks/{symbol}/bars",
                    headers=headers,
                    params=params,
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()

                bars = data.get("bars", [])
                all_bars.extend(bars)

                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break

            return all_bars
        except Exception as e:
            logger.warning(f"Alpaca historical bars failed for {symbol}: {e}")
            return None


class FinnhubProvider(DataProvider):
    """Finnhub data provider (real-time with slight delay on free tier)."""

    source = DataSource.FINNHUB
    is_realtime = True  # Few seconds delay on free tier

    BASE_URL = "https://finnhub.io/api/v1"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize with Finnhub API key.

        Args:
            api_key: Finnhub API key (or set FINNHUB_API_KEY env var)
        """
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_available():
            return None

        try:
            response = requests.get(
                f"{self.BASE_URL}/quote",
                params={"symbol": symbol, "token": self.api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("c", 0) == 0:  # No data
                return None

            return Quote(
                symbol=symbol,
                current_price=float(data.get("c", 0)),  # Current price
                open_price=float(data.get("o", 0)),  # Open
                high_price=float(data.get("h", 0)),  # High
                low_price=float(data.get("l", 0)),  # Low
                bid=float(data.get("c", 0)),  # Finnhub doesn't provide bid/ask on free tier
                ask=float(data.get("c", 0)),
                volume=0,  # Not provided in basic quote
                source=self.source,
                is_realtime=True,
            )
        except Exception as e:
            logger.warning(f"Finnhub quote failed for {symbol}: {e}")
            return None


class YahooProvider(DataProvider):
    """Yahoo Finance data provider (15-min delay - fallback only)."""

    source = DataSource.YAHOO
    is_realtime = False  # 15-minute delay!

    def is_available(self) -> bool:
        return True  # Always available, no auth needed

    def get_quote(self, symbol: str) -> Optional[Quote]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            # Get intraday data for more accurate current price
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                current_price = float(hist["Close"].iloc[-1])
                open_price = float(hist["Open"].iloc[0])
                high_price = float(hist["High"].max())
                low_price = float(hist["Low"].min())
                volume = int(hist["Volume"].sum())
            else:
                current_price = float(info.get("regularMarketPrice", 0))
                open_price = float(info.get("regularMarketOpen", 0))
                high_price = float(info.get("regularMarketDayHigh", 0))
                low_price = float(info.get("regularMarketDayLow", 0))
                volume = int(info.get("regularMarketVolume", 0))

            return Quote(
                symbol=symbol,
                current_price=current_price,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                bid=float(info.get("bid", current_price)),
                ask=float(info.get("ask", current_price)),
                volume=volume,
                source=self.source,
                is_realtime=False,  # 15-min delay!
            )
        except Exception as e:
            logger.warning(f"Yahoo quote failed for {symbol}: {e}")
            return None


class MarketDataManager:
    """
    Manages multiple data providers with automatic fallback.

    Priority order:
    1. E*TRADE (if configured and authenticated)
    2. Alpaca (if API keys configured)
    3. Finnhub (if API key configured)
    4. Yahoo Finance (fallback - 15min delay)
    """

    def __init__(
        self,
        etrade_client=None,
        alpaca_key: Optional[str] = None,
        alpaca_secret: Optional[str] = None,
        finnhub_key: Optional[str] = None,
    ):
        self.providers = []

        # Add providers in priority order
        if etrade_client:
            self.providers.append(ETradeProvider(etrade_client))

        alpaca = AlpacaProvider(alpaca_key, alpaca_secret)
        if alpaca.is_available():
            self.providers.append(alpaca)

        finnhub = FinnhubProvider(finnhub_key)
        if finnhub.is_available():
            self.providers.append(finnhub)

        # Yahoo is always available as fallback
        self.providers.append(YahooProvider())

        self._active_provider: Optional[DataProvider] = None

    @property
    def active_source(self) -> Optional[DataSource]:
        """Get the currently active data source."""
        if self._active_provider:
            return self._active_provider.source
        return None

    @property
    def is_realtime(self) -> bool:
        """Check if active provider has real-time data."""
        if self._active_provider:
            return self._active_provider.is_realtime
        return False

    def get_quote(self, symbol: str) -> Optional[Quote]:
        """
        Get quote from the first available provider.

        Tries providers in priority order and returns the first successful quote.
        """
        for provider in self.providers:
            if provider.is_available():
                quote = provider.get_quote(symbol)
                if quote:
                    self._active_provider = provider
                    return quote

        logger.error(f"All data providers failed for {symbol}")
        return None

    def get_quotes(self, symbols: list) -> Dict[str, Quote]:
        """Get quotes for multiple symbols."""
        return {symbol: self.get_quote(symbol) for symbol in symbols if self.get_quote(symbol)}

    def get_status(self) -> Dict:
        """Get status of all configured providers."""
        return {
            "providers": [
                {
                    "source": p.source.value,
                    "available": p.is_available(),
                    "realtime": p.is_realtime,
                }
                for p in self.providers
            ],
            "active": self._active_provider.source.value if self._active_provider else None,
            "is_realtime": self.is_realtime,
        }


def create_data_manager(etrade_client=None) -> MarketDataManager:
    """
    Factory function to create a MarketDataManager with env var configuration.

    Environment variables:
        ALPACA_API_KEY, ALPACA_SECRET_KEY - Alpaca credentials
        FINNHUB_API_KEY - Finnhub API key
    """
    return MarketDataManager(
        etrade_client=etrade_client,
        alpaca_key=os.environ.get("ALPACA_API_KEY"),
        alpaca_secret=os.environ.get("ALPACA_SECRET_KEY"),
        finnhub_key=os.environ.get("FINNHUB_API_KEY"),
    )
