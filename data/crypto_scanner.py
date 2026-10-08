"""Scanner pasar crypto.

Tiga jenis sinyal:
  * HIGH_VOLUME     — volume 24j di atas ambang (diperkuat lonjakan volume / gerak harga)
  * STRONG_MOMENTUM — naik kuat, di atas EMA20, volume recent meningkat, RSI belum overbought
  * EXHAUSTION      — sudah naik tinggi + RSI overbought + volume menurun ("kehabisan bahan bakar")

Semua ambang ada di ``Config`` sehingga mudah di-tuning tanpa menyentuh kode.
Catatan: ini filter riset, BUKAN sinyal entry — validasi tetap di tangan trader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from config import Config
from data.market_data import MarketDataClient, TickerInfo
from utils.helpers import calc_ema, calc_rsi, format_pct, format_usd
from utils.logger import get_logger

logger = get_logger(__name__)

MIN_CANDLES = 30  # minimal candle agar indikator bermakna


class SignalType(str, Enum):
    HIGH_VOLUME = "HIGH_VOLUME"
    STRONG_MOMENTUM = "STRONG_MOMENTUM"
    EXHAUSTION = "EXHAUSTION"


@dataclass
class CoinSignal:
    """Koin menarik beserta alasannya."""

    symbol: str
    signal_type: SignalType
    price: float
    change_24h_pct: float
    quote_volume_24h: float
    score: int
    reasons: list[str] = field(default_factory=list)
    rsi: float | None = None
    volume_ratio: float | None = None
    source: str = ""


@dataclass(frozen=True)
class _Metrics:
    """Metrik turunan dari candle yang SUDAH TUTUP (candle berjalan dibuang)."""

    rsi: float | None
    volume_ratio: float | None   # rata-rata 3 candle terakhir / rata-rata 12 candle sebelumnya
    volume_spike: float | None   # volume candle terakhir / rata-rata 24 candle sebelumnya
    ema_gap_pct: float           # jarak close terakhir dari EMA20 (%)
    upper_wick_ratio: float      # proporsi sumbu atas candle terakhir (0..1)


def _compute_metrics(df: pd.DataFrame, cfg: Config) -> _Metrics | None:
    if df.empty or len(df) < MIN_CANDLES:
        return None
    closed = df.iloc[:-1]  # buang candle yang masih berjalan agar tidak repaint
    close, volume = closed["close"], closed["volume"]

    rsi = calc_rsi(close, cfg.rsi_period)
    ema20 = float(calc_ema(close, 20).iloc[-1])
    last_close = float(close.iloc[-1])
    ema_gap = (last_close / ema20 - 1) * 100 if ema20 > 0 else 0.0

    recent_vol = float(volume.tail(3).mean())
    base_vol = float(volume.iloc[-15:-3].mean())
    volume_ratio = recent_vol / base_vol if base_vol > 0 else None

    prev_mean = float(volume.iloc[-25:-1].mean())
    volume_spike = float(volume.iloc[-1]) / prev_mean if prev_mean > 0 else None

    last = closed.iloc[-1]
    candle_range = float(last["high"] - last["low"])
    body_top = float(max(last["open"], last["close"]))
    wick = float(last["high"]) - body_top
    upper_wick = wick / candle_range if candle_range > 0 else 0.0

    return _Metrics(rsi, volume_ratio, volume_spike, ema_gap, upper_wick)


# ----------------------------------------------------------------------
# Detektor
# ----------------------------------------------------------------------
def _detect_high_volume(t: TickerInfo, m: _Metrics | None, cfg: Config) -> CoinSignal | None:
    if t.quote_volume_24h < cfg.high_volume_usd:
        return None
    score = 1
    reasons = [f"Volume 24j {format_usd(t.quote_volume_24h)} (ambang {format_usd(cfg.high_volume_usd)})"]
    if m and m.volume_spike and m.volume_spike >= cfg.volume_spike_ratio:
        score += 1
        reasons.append(f"Lonjakan volume candle terakhir {m.volume_spike:.1f}x dari rata-rata")
    if abs(t.change_24h_pct) >= cfg.momentum_min_change_pct:
        score += 1
        reasons.append(f"Harga bergerak {format_pct(t.change_24h_pct)} dalam 24 jam")
    return CoinSignal(
        t.symbol, SignalType.HIGH_VOLUME, t.price, t.change_24h_pct, t.quote_volume_24h, score, reasons,
        rsi=m.rsi if m else None, volume_ratio=m.volume_ratio if m else None, source=t.source,
    )


def _detect_momentum(t: TickerInfo, m: _Metrics | None, cfg: Config) -> CoinSignal | None:
    if m is None or m.volume_ratio is None:
        return None
    rsi_ok = m.rsi is None or m.rsi < cfg.rsi_overbought  # belum overbought
    if not (
        t.change_24h_pct >= cfg.momentum_min_change_pct
        and m.ema_gap_pct > 0
        and m.volume_ratio >= cfg.momentum_min_volume_ratio
        and rsi_ok
    ):
        return None
    score = 3
    reasons = [
        f"Naik {format_pct(t.change_24h_pct)} dalam 24 jam",
        f"Harga {m.ema_gap_pct:.1f}% di atas EMA20 (tren naik terjaga)",
        f"Volume recent {m.volume_ratio:.1f}x rata-rata — dorongan beli masih ada",
    ]
    if m.rsi is not None and m.rsi >= 55:
        score += 1
        reasons.append(f"RSI {m.rsi:.0f} — kuat tapi belum overbought")
    return CoinSignal(
        t.symbol, SignalType.STRONG_MOMENTUM, t.price, t.change_24h_pct, t.quote_volume_24h, score, reasons,
        rsi=m.rsi, volume_ratio=m.volume_ratio, source=t.source,
    )


def _detect_exhaustion(t: TickerInfo, m: _Metrics | None, cfg: Config) -> CoinSignal | None:
    if m is None or m.rsi is None or m.volume_ratio is None:
        return None
    if not (
        t.change_24h_pct >= cfg.exhaustion_min_gain_pct
        and m.rsi >= cfg.rsi_overbought
        and m.volume_ratio <= cfg.exhaustion_max_volume_ratio
    ):
        return None
    score = 3
    reasons = [
        f"Sudah naik {format_pct(t.change_24h_pct)} dalam 24 jam",
        f"RSI {m.rsi:.0f} (overbought ≥ {cfg.rsi_overbought:.0f})",
        f"Volume recent hanya {m.volume_ratio:.1f}x rata-rata — tenaga beli menipis",
    ]
    if m.upper_wick_ratio >= 0.5:
        score += 1
        reasons.append("Sumbu atas panjang pada candle terakhir — indikasi penolakan harga")
    if m.ema_gap_pct >= 8:
        score += 1
        reasons.append(f"Harga {m.ema_gap_pct:.1f}% di atas EMA20 — sangat jauh dari rata-rata")
    return CoinSignal(
        t.symbol, SignalType.EXHAUSTION, t.price, t.change_24h_pct, t.quote_volume_24h, score, reasons,
        rsi=m.rsi, volume_ratio=m.volume_ratio, source=t.source,
    )


# ----------------------------------------------------------------------
# Fungsi utama
# ----------------------------------------------------------------------
def scan_market(client: MarketDataClient, config: Config) -> list[CoinSignal]:
    """Pindai pasar dan kembalikan koin menarik (diurutkan skor, lalu volume)."""
    tickers = client.fetch_tickers()
    universe = sorted(
        (t for t in tickers if t.quote_volume_24h >= config.min_candidate_volume_usd),
        key=lambda t: t.quote_volume_24h,
        reverse=True,
    )[: config.max_candidates]
    logger.info("Scan: %d ticker, %d kandidat dianalisis.", len(tickers), len(universe))

    signals: list[CoinSignal] = []
    for ticker in universe:
        try:
            metrics = _compute_metrics(client.fetch_ohlcv(ticker.symbol), config)
            for detector in (_detect_high_volume, _detect_momentum, _detect_exhaustion):
                signal = detector(ticker, metrics, config)
                if signal:
                    signals.append(signal)
        except Exception as exc:  # satu koin bermasalah tidak boleh menghentikan scan
            logger.warning("Analisis %s dilewati: %s", ticker.symbol, exc)

    signals.sort(key=lambda s: (s.score, s.quote_volume_24h), reverse=True)
    logger.info("Scan selesai: %d sinyal ditemukan.", len(signals))
    return signals
