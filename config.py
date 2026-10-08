"""Konfigurasi terpusat GladiusAquila.

Semua setting dibaca dari environment / file ``.env`` lalu dibungkus dalam
satu objek ``Config`` yang immutable (frozen). Modul lain cukup menerima
``Config`` lewat constructor, sehingga mudah diuji dan diganti.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


# ----------------------------------------------------------------------
# Helper pembaca environment (dengan pesan error yang jelas)
# ----------------------------------------------------------------------
def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Variabel {name} harus bilangan bulat (dapat: {raw!r})") from exc


def _float(name: str, default: float) -> float:
    raw = _str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Variabel {name} harus angka (dapat: {raw!r})") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _str(name)
    if not raw:
        return default
    return raw.lower() in _TRUE_VALUES


def _list(name: str, default: str = "") -> tuple[str, ...]:
    raw = _str(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# ----------------------------------------------------------------------
# Objek konfigurasi
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    """Seluruh pengaturan agent. Nilai default aman untuk dijalankan langsung."""

    # Identitas
    agent_name: str = "Gladius Aquila"
    agent_emoji: str = "🦅"
    user_title: str = "Boss"
    timezone: str = "Asia/Jakarta"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Jadwal
    scan_interval_minutes: int = 30
    report_interval_minutes: int = 240

    # Sumber data crypto
    exchange_priority: tuple[str, ...] = ("binance", "bybit")
    quote_asset: str = "USDT"
    use_coingecko_fallback: bool = True
    coingecko_api_key: str = ""
    request_timeout_seconds: float = 20.0
    max_retries: int = 3
    excluded_bases: tuple[str, ...] = ()

    # Parameter scanner
    min_candidate_volume_usd: float = 30_000_000
    high_volume_usd: float = 300_000_000
    volume_spike_ratio: float = 2.0
    max_candidates: int = 40
    ohlcv_timeframe: str = "1h"
    ohlcv_limit: int = 100
    rsi_period: int = 14
    rsi_overbought: float = 75.0
    momentum_min_change_pct: float = 5.0
    momentum_min_volume_ratio: float = 1.2
    exhaustion_min_gain_pct: float = 12.0
    exhaustion_max_volume_ratio: float = 0.7

    # Berita Gold
    news_enabled: bool = True
    news_calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    news_cache_minutes: int = 60
    news_currencies: tuple[str, ...] = ("USD",)
    news_lookahead_minutes: int = 60
    news_post_release_minutes: int = 90

    # Anti-spam
    alert_cooldown_minutes: int = 180
    min_alert_score: int = 2
    max_alerts_per_cycle: int = 5
    error_cooldown_minutes: int = 60

    # Logging
    log_level: str = "INFO"
    log_dir: str = "logs"

    # ------------------------------------------------------------------
    @property
    def telegram_configured(self) -> bool:
        """True bila token & chat id sudah diisi."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def scan_interval_seconds(self) -> int:
        return self.scan_interval_minutes * 60

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Config":
        """Muat konfigurasi dari ``.env`` (jika ada) + environment sistem."""
        load_dotenv(dotenv_path=env_file, override=False)
        defaults = cls()
        config = cls(
            agent_name=_str("AGENT_NAME", defaults.agent_name),
            agent_emoji=_str("AGENT_EMOJI", defaults.agent_emoji),
            user_title=_str("USER_TITLE", defaults.user_title),
            timezone=_str("TIMEZONE", defaults.timezone),
            telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
            scan_interval_minutes=_int("SCAN_INTERVAL_MINUTES", defaults.scan_interval_minutes),
            report_interval_minutes=_int("REPORT_INTERVAL_MINUTES", defaults.report_interval_minutes),
            exchange_priority=tuple(x.lower() for x in _list("EXCHANGE_PRIORITY", "binance,bybit")),
            quote_asset=_str("QUOTE_ASSET", defaults.quote_asset).upper(),
            use_coingecko_fallback=_bool("USE_COINGECKO_FALLBACK", defaults.use_coingecko_fallback),
            coingecko_api_key=_str("COINGECKO_API_KEY"),
            request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", defaults.request_timeout_seconds),
            max_retries=_int("MAX_RETRIES", defaults.max_retries),
            excluded_bases=tuple(x.upper() for x in _list("EXCLUDED_BASES")),
            min_candidate_volume_usd=_float("MIN_CANDIDATE_VOLUME_USD", defaults.min_candidate_volume_usd),
            high_volume_usd=_float("HIGH_VOLUME_USD", defaults.high_volume_usd),
            volume_spike_ratio=_float("VOLUME_SPIKE_RATIO", defaults.volume_spike_ratio),
            max_candidates=_int("MAX_CANDIDATES", defaults.max_candidates),
            ohlcv_timeframe=_str("OHLCV_TIMEFRAME", defaults.ohlcv_timeframe),
            ohlcv_limit=_int("OHLCV_LIMIT", defaults.ohlcv_limit),
            rsi_period=_int("RSI_PERIOD", defaults.rsi_period),
            rsi_overbought=_float("RSI_OVERBOUGHT", defaults.rsi_overbought),
            momentum_min_change_pct=_float("MOMENTUM_MIN_CHANGE_PCT", defaults.momentum_min_change_pct),
            momentum_min_volume_ratio=_float("MOMENTUM_MIN_VOLUME_RATIO", defaults.momentum_min_volume_ratio),
            exhaustion_min_gain_pct=_float("EXHAUSTION_MIN_GAIN_PCT", defaults.exhaustion_min_gain_pct),
            exhaustion_max_volume_ratio=_float("EXHAUSTION_MAX_VOLUME_RATIO", defaults.exhaustion_max_volume_ratio),
            news_enabled=_bool("NEWS_ENABLED", defaults.news_enabled),
            news_calendar_url=_str("NEWS_CALENDAR_URL", defaults.news_calendar_url),
            news_cache_minutes=_int("NEWS_CACHE_MINUTES", defaults.news_cache_minutes),
            news_currencies=tuple(x.upper() for x in _list("NEWS_CURRENCIES", "USD")),
            news_lookahead_minutes=_int("NEWS_LOOKAHEAD_MINUTES", defaults.news_lookahead_minutes),
            news_post_release_minutes=_int("NEWS_POST_RELEASE_MINUTES", defaults.news_post_release_minutes),
            alert_cooldown_minutes=_int("ALERT_COOLDOWN_MINUTES", defaults.alert_cooldown_minutes),
            min_alert_score=_int("MIN_ALERT_SCORE", defaults.min_alert_score),
            max_alerts_per_cycle=_int("MAX_ALERTS_PER_CYCLE", defaults.max_alerts_per_cycle),
            error_cooldown_minutes=_int("ERROR_COOLDOWN_MINUTES", defaults.error_cooldown_minutes),
            log_level=_str("LOG_LEVEL", defaults.log_level).upper(),
            log_dir=_str("LOG_DIR", defaults.log_dir),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Pastikan nilai-nilai kritis masuk akal."""
        if self.scan_interval_minutes < 1:
            raise ValueError("SCAN_INTERVAL_MINUTES minimal 1")
        if self.report_interval_minutes < 1:
            raise ValueError("REPORT_INTERVAL_MINUTES minimal 1")
        if not self.exchange_priority:
            raise ValueError("EXCHANGE_PRIORITY tidak boleh kosong")
        if self.max_retries < 1:
            raise ValueError("MAX_RETRIES minimal 1")
        if self.ohlcv_limit < 40:
            raise ValueError("OHLCV_LIMIT minimal 40 agar indikator valid")
