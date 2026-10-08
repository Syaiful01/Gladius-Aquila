"""Titik masuk GladiusAquila.

Contoh:
    python main.py              # loop 24 jam (live bila Telegram sudah diisi)
    python main.py --dry-run    # pesan dicetak ke console, tidak dikirim
    python main.py --once       # satu siklus + laporan lalu selesai (untuk tes)
"""
from __future__ import annotations

import argparse
import sys

from agent import GladiusAquila
from config import Config
from utils.logger import setup_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gladius Aquila — Trading Research Agent 🦅")
    parser.add_argument("--once", action="store_true", help="jalankan satu siklus (dengan laporan) lalu keluar")
    parser.add_argument("--dry-run", action="store_true", help="cetak pesan ke console, jangan kirim ke Telegram")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Konfigurasi tidak valid: {exc}", file=sys.stderr)
        return 2

    logger = setup_logger(config.log_level, config.log_dir)
    if not config.telegram_configured and not args.dry_run:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi — otomatis memakai mode dry-run.")

    agent = GladiusAquila(config, dry_run=args.dry_run)
    if args.once:
        agent.run_cycle(force_report=True)
    else:
        agent.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
