"""Akses data pasar crypto dari sumber gratis.

Prioritas sumber:
  1. Exchange publik via CCXT (Binance -> Bybit, urutan bisa diubah di .env)
  2. CoinGecko (fallback ticker saja, tanpa OHLCV)

Rate limit ditangani lewat ``enableRateLimit`` CCXT + exponential backoff.
Tidak ada API key yang dibutuhkan (hanya endpoint publik).
"""
from __future__ import annotations

from dataclasses import dataclass

import ccxt
import pandas as pd
import requests

from config import Config
from utils.helpers import retry_call, safe_float
from utils.logger import get_logger

logger = get_logger(__name__)

# Aset yang bukan target scan momentum (stablecoin & token emas)
STABLECOINS = frozenset({
    "USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "USDD",
    "PYUSD", "USDE", "EUR", "EURI", "AEUR", "USD1", "XUSD",
})
GOLD_TOKENS = frozenset({"PAXG", "XAUT"})

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
# Proksi harga emas gratis: PAXG (token emas fisik) — BUKAN harga XAUUSD spot resmi
GOLD_PROXY_SYMBOL = "PAXG/USDT"
OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class MarketDataError(RuntimeError):
    """Semua sumber data gagal."""


@dataclass(frozen=True)
class TickerInfo:
    """Ringkasan 24 jam satu koin."""

    symbol: str            # contoh: "BTC/USDT"
    base: str              # contoh: "BTC"
    price: float
    change_24h_pct: float
    quote_volume_24h: float  # dalam quote asset (≈ USD untuk USDT)
    source: str            # "binance" | "bybit" | "coingecko"


class MarketDataClient:
    """Pembungkus CCXT + CoinGecko dengan fallback otomatis."""

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._exchanges: dict[str, ccxt.Exchange] = {}
        self._active_exchange: str | None = None
        self._excluded = STABLECOINS | GOLD_TOKENS | set(config.excluded_bases)

    # ------------------------------------------------------------------
    # Exchange helper
    # ------------------------------------------------------------------
    def _exchange(self, name: str) -> ccxt.Exchange:
        if name not in self._exchanges:
            exchange_class = getattr(ccxt, name, None)
            if exchange_class is None:
                raise MarketDataError(f"Exchange '{name}' tidak dikenal oleh CCXT.")
            self._exchanges[name] = exchange_class({
                "enableRateLimit": True,  # CCXT menahan request otomatis
                "timeout": int(self._cfg.request_timeout_seconds * 1000),
                "options": {"defaultType": "spot"},
            })
        return self._exchanges[name]

    def _ordered_exchanges(self) -> list[str]:
        """Exchange yang terakhir sukses diutamakan, sisanya mengikuti prioritas."""
        names = list(self._cfg.exchange_priority)
        if self._active_exchange in names:
            names.remove(self._active_exchange)
            names.insert(0, self._active_exchange)
        return names

    # ------------------------------------------------------------------
    # Ticker 24 jam
    # ------------------------------------------------------------------
    def fetch_tickers(self) -> list[TickerInfo]:
        """Ambil ticker semua pair spot ``*/QUOTE`` dari sumber pertama yang berhasil."""
        for name in self._ordered_exchanges():
            try:
                raw = retry_call(
                    self._exchange(name).fetch_tickers,
                    attempts=self._cfg.max_retries,
                    retry_on=(ccxt.NetworkError,),
                    logger=logger,
                )
            except (ccxt.BaseError, MarketDataError) as exc:
                logger.warning("Exchange %s gagal: %s", name, exc)
                continue
            tickers = self._parse_ccxt_tickers(raw, name)
            if tickers:
                self._active_exchange = name
                logger.info("Ticker diambil dari %s (%d pair).", name, len(tickers))
                return tickers

        if self._cfg.use_coingecko_fallback:
            try:
                tickers = self._fetch_coingecko_tickers()
                logger.warning("Memakai fallback CoinGecko (%d koin, tanpa OHLCV langsung).", len(tickers))
                return tickers
            except requests.RequestException as exc:
                logger.warning("CoinGecko gagal: %s", exc)

        raise MarketDataError("Semua sumber data ticker gagal (exchange & CoinGecko).")

    def _parse_ccxt_tickers(self, raw: dict[str, dict], source: str) -> list[TickerInfo]:
        suffix = f"/{self._cfg.quote_asset}"
        result: list[TickerInfo] = []
        for symbol, data in raw.items():
            # endswith("/USDT") otomatis membuang futures ("BTC/USDT:USDT")
            if not symbol.endswith(suffix):
                continue
            base = symbol.split("/")[0]
            if base in self._excluded:
                continue
            price = safe_float(data.get("last"))
            volume = safe_float(data.get("quoteVolume"))
            change = safe_float(data.get("percentage"))
            if not price or volume is None or change is None:
                continue
            result.append(TickerInfo(symbol, base, price, change, volume, source))
        return result

    def _fetch_coingecko_tickers(self) -> list[TickerInfo]:
        headers = {"accept": "application/json"}
        if self._cfg.coingecko_api_key:
            headers["x-cg-demo-api-key"] = self._cfg.coingecko_api_key
        params = {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 100,
            "page": 1,
            "price_change_percentage": "24h",
        }

        def _call() -> list[dict]:
            resp = requests.get(
                COINGECKO_MARKETS_URL, params=params, headers=headers,
                timeout=self._cfg.request_timeout_seconds,
            )
            resp.raise_for_status()  # 429 (rate limit) ikut di-retry
            return resp.json()

        data = retry_call(
            _call, attempts=self._cfg.max_retries, base_delay=5.0,
            retry_on=(requests.RequestException,), logger=logger,
        )
        result: list[TickerInfo] = []
        for item in data:
            base = str(item.get("symbol", "")).upper()
            price = safe_float(item.get("current_price"))
            volume = safe_float(item.get("total_volume"))
            change = safe_float(item.get("price_change_percentage_24h"))
            if not base or base in self._excluded or not price or volume is None or change is None:
                continue
            result.append(TickerInfo(
                f"{base}/{self._cfg.quote_asset}", base, price, change, volume, "coingecko",
            ))
        return result

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------
    def fetch_ohlcv(
        self, symbol: str, timeframe: str | None = None, limit: int | None = None
    ) -> pd.DataFrame:
        """Candle OHLCV sebagai DataFrame. Kosong bila semua exchange gagal."""
        timeframe = timeframe or self._cfg.ohlcv_timeframe
        limit = limit or self._cfg.ohlcv_limit
        for name in self._ordered_exchanges():
            try:
                raw = retry_call(
                    self._exchange(name).fetch_ohlcv, symbol, timeframe, limit=limit,
                    attempts=self._cfg.max_retries,
                    retry_on=(ccxt.NetworkError,),
                    logger=logger,
                )
            except ccxt.BadSymbol:
                continue  # pair tidak ada di exchange ini, coba berikutnya
            except (ccxt.BaseError, MarketDataError) as exc:
                logger.debug("OHLCV %s dari %s gagal: %s", symbol, name, exc)
                continue
            if raw:
                return self._to_dataframe(raw)
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    @staticmethod
    def _to_dataframe(raw: list[list[float]]) -> pd.DataFrame:
        df = pd.DataFrame(raw, columns=OHLCV_COLUMNS)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    # ------------------------------------------------------------------
    # Proksi harga Gold
    # ------------------------------------------------------------------
    def fetch_gold_proxy(self) -> TickerInfo | None:
        """Harga PAXG/USDT sebagai *proksi* emas (selisih kecil vs XAUUSD spot)."""
        for name in self._ordered_exchanges():
            try:
                data = retry_call(
                    self._exchange(name).fetch_ticker, GOLD_PROXY_SYMBOL,
                    attempts=self._cfg.max_retries,
                    retry_on=(ccxt.NetworkError,),
                    logger=logger,
                )
            except (ccxt.BaseError, MarketDataError) as exc:
                logger.debug("Proksi emas dari %s gagal: %s", name, exc)
                continue
            price = safe_float(data.get("last"))
            if price:
                return TickerInfo(
                    symbol=GOLD_PROXY_SYMBOL,
                    base="PAXG",
                    price=price,
                    change_24h_pct=safe_float(data.get("percentage"), 0.0) or 0.0,
                    quote_volume_24h=safe_float(data.get("quoteVolume"), 0.0) or 0.0,
                    source=name,
                )
        return None
