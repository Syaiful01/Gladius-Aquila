"""Pemantau berita ekonomi high-impact untuk XAUUSD (Gold).

Arsitektur sengaja dibuat berlapis supaya mudah di-upgrade:

    EventProvider (sumber data)  ->  analyze_event() (aturan bias)  ->  GoldNewsMonitor (jadwal/alert)

* Ganti sumber data: buat class baru yang punya ``fetch_events()`` lalu
  suntikkan ke ``GoldNewsMonitor(config, provider=...)``.
* Ganti/tambah aturan bias: edit ``_POSITIVE_USD_KEYWORDS`` / ``_NEGATIVE_USD_KEYWORDS``
  atau tulis ulang ``analyze_event()``.

PENTING (keterbatasan jujur): feed gratis ForexFactory biasanya TIDAK menyertakan
nilai *actual* — hanya jadwal, forecast, dan previous. Selama ``actual`` kosong,
agent hanya mengirim peringatan dini (pre-event). Analisis Buy/Sell bias aktif
otomatis begitu provider mengisi ``actual`` (mis. provider berbayar / scraper).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol

import requests

from config import Config
from utils.helpers import TTLCache, retry_call, utc_now
from utils.logger import get_logger

logger = get_logger(__name__)


class GoldBias(str, Enum):
    BULLISH = "BULLISH"   # cenderung mendukung Buy Gold
    BEARISH = "BEARISH"   # cenderung mendukung Sell Gold
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class EconomicEvent:
    title: str
    currency: str
    impact: str                  # "High" | "Medium" | "Low" | "Holiday"
    time_utc: datetime
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None

    @property
    def event_id(self) -> str:
        """ID stabil untuk deduplikasi alert."""
        return f"{self.time_utc.isoformat()}|{self.currency}|{self.title}"


@dataclass(frozen=True)
class GoldImpact:
    bias: GoldBias
    reason: str
    confidence: str  # "low" | "medium"


@dataclass
class NewsCheckResult:
    """Hasil satu kali pengecekan kalender."""

    upcoming: list[tuple[EconomicEvent, GoldImpact]] = field(default_factory=list)
    released: list[tuple[EconomicEvent, GoldImpact]] = field(default_factory=list)
    next_events: list[EconomicEvent] = field(default_factory=list)


# ----------------------------------------------------------------------
# Sumber data
# ----------------------------------------------------------------------
class EventProvider(Protocol):
    """Kontrak sumber kalender ekonomi. Implementasikan ini untuk upgrade."""

    def fetch_events(self) -> list[EconomicEvent]: ...


class FaireEconomyProvider:
    """Mirror JSON gratis kalender ForexFactory (minggu berjalan), dengan cache."""

    _HEADERS = {"User-Agent": "Mozilla/5.0 (GladiusAquila research agent)"}

    def __init__(self, url: str, cache_minutes: int, timeout: float, retries: int) -> None:
        self._url = url
        self._timeout = timeout
        self._retries = retries
        self._cache: TTLCache[list[EconomicEvent]] = TTLCache(cache_minutes * 60)

    def fetch_events(self) -> list[EconomicEvent]:
        cached = self._cache.get()
        if cached is not None:
            return cached
        try:
            events = self._download()
        except (requests.RequestException, ValueError) as exc:
            stale = self._cache.get_stale()
            if stale is not None:  # lebih baik data lama daripada buta
                logger.warning("Kalender gagal diperbarui (%s). Memakai cache lama.", exc)
                return stale
            raise
        self._cache.set(events)
        logger.info("Kalender ekonomi diperbarui: %d event.", len(events))
        return events

    def _download(self) -> list[EconomicEvent]:
        def _call() -> list[dict]:
            resp = requests.get(self._url, headers=self._HEADERS, timeout=self._timeout)
            resp.raise_for_status()  # 429 ikut di-retry dengan backoff
            return resp.json()

        raw = retry_call(
            _call, attempts=self._retries, base_delay=5.0,
            retry_on=(requests.RequestException,), logger=logger,
        )
        events: list[EconomicEvent] = []
        for item in raw:
            event = self._parse_event(item)
            if event:
                events.append(event)
        return events

    @staticmethod
    def _parse_event(item: dict) -> EconomicEvent | None:
        try:
            when = datetime.fromisoformat(str(item["date"]).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return EconomicEvent(
                title=str(item["title"]).strip(),
                currency=str(item.get("country", "")).strip().upper(),
                impact=str(item.get("impact", "")).strip().title(),
                time_utc=when.astimezone(timezone.utc),
                forecast=(str(item.get("forecast")).strip() or None) if item.get("forecast") is not None else None,
                previous=(str(item.get("previous")).strip() or None) if item.get("previous") is not None else None,
                actual=(str(item.get("actual")).strip() or None) if item.get("actual") is not None else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.debug("Event kalender dilewati (%s): %r", exc, item)
            return None


# ----------------------------------------------------------------------
# Analisis bias
# ----------------------------------------------------------------------
# Data yang "lebih tinggi dari forecast" = USD menguat = tekanan turun untuk Gold
_POSITIVE_USD_KEYWORDS = (
    "non-farm", "nonfarm", "nfp", "cpi", "ppi", "pce", "retail sales", "gdp",
    "ism", "pmi", "average hourly earnings", "adp", "durable goods", "jolts",
    "employment change", "federal funds rate", "interest rate",
)
# Data yang "lebih tinggi dari forecast" = USD melemah = dukungan naik untuk Gold
_NEGATIVE_USD_KEYWORDS = ("unemployment rate", "unemployment claims", "jobless claims")


def _parse_number(text: str | None) -> float | None:
    """'3.2%' -> 3.2 ; '175K' -> 175000 ; '-0.1M' -> -100000 ; selain itu None."""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace("%", "")
    multiplier = 1.0
    if cleaned and cleaned[-1].upper() in "KMBT":
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[cleaned[-1].upper()]
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _polarity(title: str) -> int:
    """+1: lebih tinggi = USD kuat; -1: lebih tinggi = USD lemah; 0: tidak ada aturan."""
    lowered = title.lower()
    if any(k in lowered for k in _NEGATIVE_USD_KEYWORDS):
        return -1
    if any(k in lowered for k in _POSITIVE_USD_KEYWORDS):
        return +1
    return 0


def analyze_event(event: EconomicEvent) -> GoldImpact:
    """Bias sederhana Gold berdasarkan actual vs forecast (aturan heuristik, bukan ramalan)."""
    if event.actual is None:
        return GoldImpact(
            GoldBias.NEUTRAL,
            "Data belum rilis — volatilitas tinggi & spread melebar sering terjadi saat rilis.",
            "low",
        )
    actual, forecast = _parse_number(event.actual), _parse_number(event.forecast)
    if actual is None or forecast is None:
        return GoldImpact(
            GoldBias.NEUTRAL,
            "Event naratif/tanpa angka pembanding — nilai nada hawkish/dovish secara manual.",
            "low",
        )
    polarity = _polarity(event.title)
    if polarity == 0:
        return GoldImpact(GoldBias.NEUTRAL, "Belum ada aturan bias untuk jenis data ini.", "low")

    deviation = actual - forecast
    if deviation == 0:
        return GoldImpact(GoldBias.NEUTRAL, f"Actual {event.actual} sesuai forecast — kejutan minim.", "low")

    relative = abs(deviation) / abs(forecast) if forecast else abs(deviation)
    usd_stronger = (deviation > 0) == (polarity > 0)
    bias = GoldBias.BEARISH if usd_stronger else GoldBias.BULLISH
    reason = (
        f"Actual {event.actual} vs forecast {event.forecast}: USD cenderung "
        f"{'menguat' if usd_stronger else 'melemah'}, tekanan "
        f"{'turun' if usd_stronger else 'naik'} untuk Gold."
    )
    return GoldImpact(bias, reason, "medium" if relative >= 0.10 else "low")


# ----------------------------------------------------------------------
# Monitor
# ----------------------------------------------------------------------
class GoldNewsMonitor:
    """Memfilter event high-impact dan menentukan mana yang perlu di-alert."""

    def __init__(self, config: Config, provider: EventProvider | None = None) -> None:
        self._cfg = config
        self._provider: EventProvider = provider or FaireEconomyProvider(
            url=config.news_calendar_url,
            cache_minutes=config.news_cache_minutes,
            timeout=config.request_timeout_seconds,
            retries=config.max_retries,
        )

    def high_impact_events(self) -> list[EconomicEvent]:
        currencies = set(self._cfg.news_currencies)
        events = [
            e for e in self._provider.fetch_events()
            if e.impact == "High" and (not currencies or e.currency in currencies)
        ]
        return sorted(events, key=lambda e: e.time_utc)

    def check(self) -> NewsCheckResult:
        """Cari event yang akan datang (peringatan dini) dan yang baru rilis."""
        now = utc_now()
        events = self.high_impact_events()
        lookahead = timedelta(minutes=self._cfg.news_lookahead_minutes)
        post_window = timedelta(minutes=self._cfg.news_post_release_minutes)

        result = NewsCheckResult()
        for event in events:
            if now < event.time_utc <= now + lookahead:
                result.upcoming.append((event, analyze_event(event)))
            elif event.actual is not None and event.time_utc <= now <= event.time_utc + post_window:
                result.released.append((event, analyze_event(event)))
        result.next_events = [e for e in events if e.time_utc > now][:3]
        return result
