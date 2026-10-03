# Personal Expense Tracker

Aplikasi pengelolaan keuangan pribadi dengan dua antarmuka — **CLI** dan **Telegram Bot** — yang berbagi satu database **SQLite**.

> Status: kerangka proyek (fase 0). Fitur belum tersedia.

## Kebutuhan

- Python 3.11 atau lebih baru
- Git

## Instalasi

1. **Clone repositori**

   ```bash
   git clone <url-repo> personal_expense_tracker
   cd personal_expense_tracker
   ```

2. **Buat dan aktifkan virtual environment**

   Windows (PowerShell):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   Linux / macOS:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Pasang dependensi**

   ```bash
   pip install -r requirements.txt
   ```

4. **Siapkan file `.env`**

   Windows: `copy .env.example .env` — Linux/macOS: `cp .env.example .env`

   Lalu isi `TELEGRAM_BOT_TOKEN` dan `ALLOWED_TELEGRAM_IDS` (boleh dikosongkan dulu jika belum memakai bot).

5. **Jalankan**

   ```bash
   python main.py
   ```

## Menjalankan tes

```bash
pytest
```

## Konfigurasi

Semua pengaturan ada di [`config.py`](config.py):

| Pengaturan | Default | Keterangan |
|---|---|---|
| `DB_PATH` | `data/expense_tracker.db` | Bisa diubah lewat `.env` |
| `TIMEZONE_NAME` | `Asia/Jakarta` | Zona waktu semua tanggal |
| `BUDGET_WARNING_PERCENT` | `80` | Peringatan "mendekati batas" |
| `BUDGET_EXCEEDED_PERCENT` | `100` | Peringatan "melewati batas" |
| `ALLOWED_TELEGRAM_IDS` | kosong | Whitelist user ID bot, dari `.env` |

## Struktur folder

```
personal_expense_tracker/
├── main.py          # titik masuk
├── config.py        # konfigurasi terpusat
├── database/        # koneksi, skema, migrasi, query
├── models/          # dataclass
├── services/        # logika bisnis
├── reports/         # laporan + export
├── charts/          # grafik matplotlib
├── cli/             # menu terminal
├── bot/             # handler Telegram
├── utils/           # validasi, format rupiah, tanggal
├── tests/           # pytest
├── data/            # file .db (dibuat otomatis, di-gitignore)
└── reports_output/  # hasil laporan/chart (dibuat otomatis, di-gitignore)
```
