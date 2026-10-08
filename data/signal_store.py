"""Penyimpanan riwayat sinyal ke SQLite lokal.

Database disimpan di direktori ``data/`` dengan nama ``signals.db``.
Thread-safe karena menggunakan ``threading.Lock`` untuk setiap operasi tulis.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from data.crypto_scanner import CoinSignal
from utils.helpers import utc_now
from utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent / "signals.db"

_DDL = """
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    signal_type   TEXT    NOT NULL,
    price         REAL    NOT NULL,
    change_24h_pct REAL   NOT NULL,
    volume_24h_usd REAL   NOT NULL,
    score         INTEGER NOT NULL,
    rsi           REAL,
    volume_ratio  REAL,
    reasons       TEXT    NOT NULL,   -- JSON array
    source        TEXT    NOT NULL,
    created_at    TEXT    NOT NULL    -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol  ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_type    ON signals(signal_type);
"""


class SignalStore:
    """Menyimpan sinyal CoinSignal ke SQLite dan menyediakan query historis.

    Penggunaan:
        store = SignalStore()
        store.save(signal)
        rows = store.get_recent(hours=24)
    """

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
        logger.debug("SignalStore siap: %s", self._db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Tulis
    # ------------------------------------------------------------------
    def save(self, signal: CoinSignal) -> None:
        """Simpan satu sinyal ke database. Thread-safe."""
        row = (
            signal.symbol,
            signal.signal_type.value,
            signal.price,
            signal.change_24h_pct,
            signal.quote_volume_24h,
            signal.score,
            signal.rsi,
            signal.volume_ratio,
            json.dumps(list(signal.reasons), ensure_ascii=False),
            signal.source,
            utc_now().isoformat(),
        )
        sql = """
            INSERT INTO signals
              (symbol, signal_type, price, change_24h_pct, volume_24h_usd,
               score, rsi, volume_ratio, reasons, source, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._lock:
            with self._connect() as conn:
                conn.execute(sql, row)
        logger.debug("Sinyal disimpan: %s %s", signal.symbol, signal.signal_type.value)

    def save_many(self, signals: list[CoinSignal]) -> None:
        """Simpan banyak sinyal sekaligus (lebih efisien dari loop save())."""
        if not signals:
            return
        rows = [
            (
                s.symbol,
                s.signal_type.value,
                s.price,
                s.change_24h_pct,
                s.quote_volume_24h,
                s.score,
                s.rsi,
                s.volume_ratio,
                json.dumps(list(s.reasons), ensure_ascii=False),
                s.source,
                utc_now().isoformat(),
            )
            for s in signals
        ]
        sql = """
            INSERT INTO signals
              (symbol, signal_type, price, change_24h_pct, volume_24h_usd,
               score, rsi, volume_ratio, reasons, source, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._lock:
            with self._connect() as conn:
                conn.executemany(sql, rows)
        logger.info("Disimpan %d sinyal ke database.", len(signals))

    # ------------------------------------------------------------------
    # Baca
    # ------------------------------------------------------------------
    def get_recent(self, hours: int = 24, limit: int = 200) -> list[dict]:
        """Kembalikan sinyal N jam terakhir sebagai list dict."""
        cutoff = datetime.now(timezone.utc).replace(
            hour=datetime.now(timezone.utc).hour - hours
            if datetime.now(timezone.utc).hour >= hours else 0,
            minute=0 if datetime.now(timezone.utc).hour < hours else datetime.now(timezone.utc).minute,
            second=0, microsecond=0
        )
        # Cara yang lebih sederhana: gunakan SQLite datetime arithmetic
        sql = """
            SELECT * FROM signals
            WHERE created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            LIMIT ?
        """
        offset = f"-{hours} hours"
        with self._connect() as conn:
            rows = conn.execute(sql, (offset, limit)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Statistik ringkas: jumlah per tipe, top 5 symbol."""
        with self._connect() as conn:
            count_by_type = {
                row["signal_type"]: row["cnt"]
                for row in conn.execute(
                    "SELECT signal_type, COUNT(*) as cnt FROM signals "
                    "WHERE created_at >= datetime('now', '-24 hours') GROUP BY signal_type"
                ).fetchall()
            }
            top_symbols = [
                dict(row)
                for row in conn.execute(
                    "SELECT symbol, COUNT(*) as cnt, MAX(score) as max_score "
                    "FROM signals WHERE created_at >= datetime('now', '-24 hours') "
                    "GROUP BY symbol ORDER BY cnt DESC LIMIT 5"
                ).fetchall()
            ]
            total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        return {
            "total_all_time": total,
            "last_24h_by_type": count_by_type,
            "top_symbols_24h": top_symbols,
        }

    def get_symbol_history(self, symbol: str, limit: int = 50) -> list[dict]:
        """Riwayat sinyal untuk satu symbol tertentu."""
        sql = """
            SELECT * FROM signals WHERE symbol = ?
            ORDER BY created_at DESC LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (symbol, limit)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Util
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["reasons"] = json.loads(d["reasons"])
        except (json.JSONDecodeError, KeyError):
            d["reasons"] = []
        return d

    def purge_old(self, keep_days: int = 30) -> int:
        """Hapus sinyal yang lebih lama dari ``keep_days`` hari. Kembalikan jumlah baris dihapus."""
        sql = "DELETE FROM signals WHERE created_at < datetime('now', ?)"
        offset = f"-{keep_days} days"
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(sql, (offset,))
                deleted = cur.rowcount
        if deleted:
            logger.info("Purge database: %d sinyal lama dihapus.", deleted)
        return deleted
