# 🦅 GladiusAquila — Trading Research Agent 24 Jam

Agent Python yang berpatroli di pasar **tanpa henti** dan melapor ke Telegram dengan karakter
**Gladius Aquila**: prajurit elit yang selalu memanggil Anda **"Boss"**.

> ⚠️ **Hanya riset + notifikasi.** Tidak ada auto-trading. Semua sinyal adalah filter
> heuristik, bukan rekomendasi beli/jual. Uji dan validasi sendiri sebelum memakainya
> untuk keputusan nyata.

## Apa yang dilakukan

| Misi | Cara kerja |
|------|-----------|
| **Berita XAUUSD** | Membaca kalender ekonomi ForexFactory (mirror JSON gratis), memfilter event *High impact* (NFP, CPI, FOMC, dst.), kirim peringatan dini sebelum rilis, dan bias Buy/Sell Gold dari *actual vs forecast* bila data actual tersedia. |
| **Volume besar** | Koin spot USDT dengan volume 24j di atas ambang. Alert hanya bila diperkuat lonjakan volume atau gerak harga (skor ≥ `MIN_ALERT_SCORE`). |
| **Momentum kuat** | Naik ≥ X% / 24j, harga di atas EMA20, volume recent meningkat, RSI belum overbought. |
| **Exhaustion** | Sudah naik tinggi + RSI overbought + volume recent menurun (bonus: sumbu atas panjang, jauh dari EMA20). |
| **Laporan** | Ringkasan berkala (senyap) + alert penting (dengan bunyi). Anti-spam via cooldown. |

## Struktur

```
GladiusAquila/
├── .env.example            # template konfigurasi
├── requirements.txt
├── config.py               # baca .env -> objek Config (frozen)
├── main.py                 # titik masuk (CLI: --once, --dry-run)
├── agent.py                # class GladiusAquila: run_cycle() & start()
├── character/persona.py    # semua template pesan berkarakter
├── notifications/telegram_bot.py   # TelegramNotifier (retry, HTML, dry-run)
├── data/
│   ├── market_data.py      # CCXT (Binance/Bybit) + fallback CoinGecko
│   ├── crypto_scanner.py   # scan_market(): volume / momentum / exhaustion
│   └── gold_news.py        # kalender + analisis bias Gold (provider bisa diganti)
└── utils/
    ├── helpers.py          # RSI/EMA, format angka, retry, cache TTL
    └── logger.py           # logging console + file berputar
```

## Setup

Butuh **Python 3.10+**.

```bash
cd GladiusAquila
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

### Isi `.env`

1. Buka Telegram → **@BotFather** → `/newbot` → salin token ke `TELEGRAM_BOT_TOKEN`.
2. Kirim pesan apa saja ke bot Anda, lalu ambil chat id lewat **@userinfobot** atau
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → isi `TELEGRAM_CHAT_ID`.
3. Sesuaikan `TIMEZONE`, `SCAN_INTERVAL_MINUTES`, dan ambang scanner bila perlu.
   Semua parameter dijelaskan di `.env.example`.

## Menjalankan

```bash
python run_web.py          # 🔥 Tampilan Web AI Command Center (Buka http://localhost:8888)
python main.py --dry-run   # uji tanpa Telegram: pesan dicetak ke console
python main.py --once      # satu siklus + laporan, lalu selesai
python main.py             # mode 24 jam background (Ctrl+C untuk berhenti)
```

Bila token/chat id kosong, agent otomatis berjalan dalam mode dry-run.

## Deploy 24 jam (VPS / hosting gratis)

**Systemd (VPS Linux)** — `/etc/systemd/system/gladius.service`:

```ini
[Unit]
Description=Gladius Aquila Trading Research Agent
After=network-online.target

[Service]
WorkingDirectory=/opt/GladiusAquila
ExecStart=/opt/GladiusAquila/.venv/bin/python main.py
Restart=always
RestartSec=10
User=gladius

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now gladius
journalctl -u gladius -f          # pantau log
```

**Alternatif ringan:** `tmux`/`screen` + `python main.py`, atau `nohup python main.py &`.

Catatan hosting:
- Pilihan gratis yang realistis: VPS *always-free* (mis. Oracle Cloud) atau mini-PC/Raspberry Pi di rumah.
  PaaS gratis yang "tidur" saat idle tidak cocok untuk loop 24 jam.
- **Binance memblokir beberapa region/IP datacenter** (error HTTP 451). Jika terjadi, ubah
  `EXCHANGE_PRIORITY=bybit,binance`. Bila semua exchange gagal, agent turun ke CoinGecko
  (hanya ticker; analisis RSI/volume-trend tidak tersedia).

## Karakter Gladius Aquila

Semua pesan lewat `character/persona.py`. `TelegramNotifier.send_message()` otomatis membungkus
teks dengan persona; `send_alert()`/`send_report()` menerima output template `Persona` — jadi
tidak ada pesan yang keluar tanpa karakter. Mengubah gaya bicara = edit satu file itu.

Template: online/offline, laporan rutin, alert volume, momentum, exhaustion, berita Gold, dan error.

## Keterbatasan (jujur)

- **Nilai *actual* berita**: feed gratis kalender umumnya hanya berisi jadwal/forecast/previous.
  Selama `actual` kosong, agent hanya mengirim peringatan dini; bias Buy/Sell aktif otomatis
  begitu provider mengisi `actual`. Aturan bias di `gold_news.py` adalah heuristik sederhana.
- **Harga Gold** di laporan memakai proksi **PAXG/USDT**, bukan XAUUSD spot broker.
- Scan berjalan per `SCAN_INTERVAL_MINUTES` (default 30). Reaksi detik-ke-detik saat rilis berita
  butuh loop berita terpisah yang lebih cepat.
- Ambang scanner adalah titik awal, **belum divalidasi lewat backtest**. Anggap sebagai pemfilter
  watchlist, bukan sinyal entry.

## Ide pengembangan

- Provider kalender baru yang menyertakan *actual* (implementasikan `EventProvider.fetch_events()`).
- Loop berita cepat di sekitar jam rilis; command Telegram interaktif (`/status`, `/scan`).
- Data futures: funding rate, open interest, likuidasi.
- Backtest ambang scanner dan simpan riwayat sinyal ke SQLite.
