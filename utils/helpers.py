"""Fungsi bantu umum: format angka, indikator teknikal, retry, cache TTL."""
from __future__ import annotations

import html
import logging
import math
import time
from datetime import datetime, timezone
from typing import Callable, Generic, TypeVar

import pandas as pd

T = TypeVar("T")


# ----------------------------------------------------------------------
# Teks & format
# ----------------------------------------------------------------------
def esc(value: object) -> str:
    """Escape teks dinamis agar aman dipakai di Telegram parse_mode=HTML."""
    return html.escape(str(value), quote=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: object, default: float | None = None) -> float | None:
    """Konversi ke float; kembalikan ``default`` bila None/NaN/Inf/invalid."""
    if value is None:
        return default
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def format_usd(value: float) -> str:
    """12_345_678 -> $12.35M"""
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"${value / threshold:,.2f}{suffix}"
    return f"${value:,.2f}"


def format_price(price: float) -> str:
    """Jumlah desimal menyesuaikan besar harga."""
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    if price >= 0.01:
        return f"{price:.5f}"
    return f"{price:.8f}"


def format_pct(value: float) -> str:
    return f"{value:+.2f}%"


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    """Pecah teks panjang per baris agar muat batas 4096 karakter Telegram."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


# ----------------------------------------------------------------------
# Indikator teknikal
# ----------------------------------------------------------------------
def calc_rsi(close: pd.Series, period: int = 14) -> float | None:
    """RSI Wilder (EMA alpha=1/period). Mengembalikan nilai terakhir atau None."""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100.0)  # tidak ada loss sama sekali -> RSI 100
    return safe_float(rsi.iloc[-1])


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ----------------------------------------------------------------------
# Ketahanan (resilience)
# ----------------------------------------------------------------------
def retry_call(
    func: Callable[..., T],
    *args: object,
    attempts: int = 3,
    base_delay: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    logger: logging.Logger | None = None,
    **kwargs: object,
) -> T:
    """Jalankan ``func`` dengan exponential backoff untuk exception tertentu."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except retry_on as exc:
            last_exc = exc
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            if logger:
                logger.warning(
                    "Percobaan %d/%d gagal (%s). Ulangi dalam %.1f detik.",
                    attempt, attempts, exc, delay,
                )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class TTLCache(Generic[T]):
    """Cache satu nilai dengan masa berlaku; bisa mengembalikan nilai basi saat gagal."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._value: T | None = None
        self._stored_at: float | None = None

    def get(self) -> T | None:
        """Nilai bila masih segar, selain itu None."""
        if self._value is None or self._stored_at is None:
            return None
        if time.monotonic() - self._stored_at > self._ttl:
            return None
        return self._value

    def get_stale(self) -> T | None:
        """Nilai terakhir walau sudah kedaluwarsa (untuk fallback)."""
        return self._value

    def set(self, value: T) -> None:
        self._value = value
        self._stored_at = time.monotonic()
