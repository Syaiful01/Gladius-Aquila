"""Karakter Gladius Aquila 🦅.

Semua pesan Telegram dibangun di sini, sehingga gaya bicara konsisten:
prajurit elit yang melapor dari medan perang (pasar) kepada "Boss".
Output berformat HTML Telegram; semua teks dinamis di-escape.
"""
from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import Config
from data.crypto_scanner import CoinSignal, SignalType
from data.gold_news import EconomicEvent, GoldBias, GoldImpact
from data.market_data import TickerInfo
from utils.helpers import esc, format_pct, format_price, format_usd, utc_now

DISCLAIMER = "<i>Laporan riset, bukan saran finansial. Keputusan eksekusi tetap di tangan Boss.</i>"

_BIAS_LABEL = {
    GoldBias.BULLISH: "🟢 BULLISH — condong mendukung Buy Gold",
    GoldBias.BEARISH: "🔴 BEARISH — condong mendukung Sell Gold",
    GoldBias.NEUTRAL: "⚪ NETRAL — belum ada arah jelas",
}


class Persona:
    """Pembungkus pesan berkarakter. Tidak melakukan I/O."""

    def __init__(self, config: Config) -> None:
        self._cfg = config
        self._boss = esc(config.user_title)
        try:
            self._tz = ZoneInfo(config.timezone)
        except ZoneInfoNotFoundError:
            self._tz = ZoneInfo("UTC")

    # ------------------------------------------------------------------
    # Utilitas internal
    # ------------------------------------------------------------------
    def _header(self, title: str) -> str:
        return f"{esc(self._cfg.agent_emoji)} <b>{esc(self._cfg.agent_name.upper())}</b> — {esc(title)}"

    def _now_local(self) -> str:
        return utc_now().astimezone(self._tz).strftime("%d %b %Y %H:%M %Z")

    def _local(self, dt: datetime) -> str:
        return dt.astimezone(self._tz).strftime("%d %b %H:%M %Z")

    @staticmethod
    def _pick(options: list[str]) -> str:
        return random.choice(options)

    def _coin_block(self, s: CoinSignal) -> str:
        rsi = f" | RSI {s.rsi:.0f}" if s.rsi is not None else ""
        lines = [
            f"• <b>{esc(s.symbol)}</b>  <code>{format_price(s.price)}</code>  ({format_pct(s.change_24h_pct)})",
            f"  Vol 24j {format_usd(s.quote_volume_24h)}{rsi}",
        ]
        lines += [f"  – {esc(r)}" for r in s.reasons]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Template pesan
    # ------------------------------------------------------------------
    def say(self, text: str) -> str:
        """Bungkus teks bebas dengan karakter (dipakai ``send_message``)."""
        return f"{self._header('Pesan')}\n\n{esc(text)}"

    def online(self, interval_minutes: int) -> str:
        opener = self._pick([
            f"Siap bertugas, {self._boss}! Pasukan sudah di posisi.",
            f"Gladius Aquila melapor, {self._boss}. Medan perang sudah dalam pengawasan.",
            f"Elang sudah mengudara, {self._boss}. Tidak ada pergerakan yang lolos dari mata kami.",
        ])
        return (
            f"{self._header('ONLINE')}\n\n{opener}\n\n"
            f"🎯 Misi: pantau berita high-impact <b>XAUUSD</b> &amp; sisir pasar crypto "
            f"(volume besar, momentum, exhaustion).\n"
            f"⏱ Patroli setiap <b>{interval_minutes} menit</b>.\n"
            f"🕒 {esc(self._now_local())}\n\n"
            f"Mode: <b>riset &amp; notifikasi</b>. Tidak ada eksekusi order."
        )

    def shutdown(self) -> str:
        return (
            f"{self._header('OFFLINE')}\n\n"
            f"Penarikan pasukan, {self._boss}. Patroli dihentikan — siap kembali begitu dipanggil.\n"
            f"🕒 {esc(self._now_local())}"
        )

    def routine_report(
        self,
        cycle: int,
        signals: list[CoinSignal],
        next_events: list[EconomicEvent],
        gold_proxy: TickerInfo | None,
        top_n: int = 3,
    ) -> str:
        parts = [
            self._header("Laporan Situasi Medan Perang"),
            f"🕒 {esc(self._now_local())} • siklus #{cycle}",
            "",
            self._pick([
                f"{self._boss}, berikut kondisi terkini di garis depan.",
                f"Laporan masuk, {self._boss}. Situasi saya rangkum singkat dan jelas.",
            ]),
            "",
            "🥇 <b>GOLD (XAUUSD)</b>",
        ]
        if gold_proxy:
            parts.append(
                f"Proksi PAXG/USDT: <code>{format_price(gold_proxy.price)}</code> "
                f"({format_pct(gold_proxy.change_24h_pct)} 24j) <i>— bukan harga spot resmi</i>"
            )
        else:
            parts.append("Proksi harga emas: tidak tersedia saat ini.")
        if next_events:
            parts.append("Agenda high-impact terdekat:")
            parts += [f"• {esc(self._local(e.time_utc))} — <b>{esc(e.currency)}</b> {esc(e.title)}" for e in next_events]
        else:
            parts.append("Tidak ada event high-impact terjadwal dalam waktu dekat.")

        sections = (
            (SignalType.STRONG_MOMENTUM, "🔥 <b>MOMENTUM KUAT</b>"),
            (SignalType.EXHAUSTION, "⛽ <b>POTENSI EXHAUSTION</b>"),
            (SignalType.HIGH_VOLUME, "📊 <b>VOLUME BESAR</b>"),
        )
        for signal_type, title in sections:
            subset = [s for s in signals if s.signal_type == signal_type][:top_n]
            parts += ["", title]
            parts += [self._coin_block(s) for s in subset] or ["Tidak ada target yang memenuhi kriteria."]

        parts += ["", DISCLAIMER]
        return "\n".join(parts)

    def volume_alert(self, s: CoinSignal) -> str:
        return (
            f"{self._header('ALERT VOLUME')}\n\n"
            f"{self._boss}, ada pergerakan pasukan besar di sektor ini!\n\n"
            f"{self._coin_block(s)}\n\n{DISCLAIMER}"
        )

    def momentum_alert(self, s: CoinSignal) -> str:
        return (
            f"{self._header('ALERT MOMENTUM')}\n\n"
            f"{self._boss}, serangan sedang berlangsung dan tenaganya masih terjaga.\n\n"
            f"{self._coin_block(s)}\n\n{DISCLAIMER}"
        )

    def exhaustion_alert(self, s: CoinSignal) -> str:
        return (
            f"{self._header('ALERT EXHAUSTION')}\n\n"
            f"{self._boss}, pasukan di sektor ini mulai kehabisan amunisi. "
            f"Waspada potensi koreksi.\n\n"
            f"{self._coin_block(s)}\n\n{DISCLAIMER}"
        )

    def gold_news_alert(self, event: EconomicEvent, impact: GoldImpact, phase: str) -> str:
        """``phase``: ``"upcoming"`` (peringatan dini) atau ``"released"`` (pasca-rilis)."""
        if phase == "upcoming":
            minutes = max(0, int((event.time_utc - utc_now()).total_seconds() // 60))
            intro = f"{self._boss}, badai data ekonomi mendekat — rilis dalam ±{minutes} menit."
            title = "ALERT BERITA GOLD"
        else:
            intro = f"{self._boss}, data sudah dirilis. Ini pembacaan awal untuk Gold."
            title = "HASIL RILIS BERITA"
        lines = [
            f"{self._header(title)}\n",
            intro,
            "",
            f"📰 <b>{esc(event.currency)} — {esc(event.title)}</b>",
            f"🕒 {esc(self._local(event.time_utc))}",
            f"Forecast: <code>{esc(event.forecast or '-')}</code> | "
            f"Previous: <code>{esc(event.previous or '-')}</code> | "
            f"Actual: <code>{esc(event.actual or '-')}</code>",
            "",
            f"🥇 Bias XAUUSD: {_BIAS_LABEL[impact.bias]}",
            f"Alasan: {esc(impact.reason)}",
            f"Keyakinan: {esc(impact.confidence)}",
            "",
            "⚠️ Hindari entry impulsif di menit pertama rilis; spread &amp; slippage biasanya melebar.",
            DISCLAIMER,
        ]
        return "\n".join(lines)

    def error(self, context: str, error: BaseException) -> str:
        detail = esc(f"{type(error).__name__}: {error}"[:300])
        return (
            f"{self._header('LAPORAN GANGGUAN')}\n\n"
            f"{self._boss}, ada hambatan di jalur <b>{esc(context)}</b>. "
            f"Pasukan tetap bertahan dan mencoba lagi di patroli berikutnya.\n\n"
            f"<code>{detail}</code>"
        )
