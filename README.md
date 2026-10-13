# Personal Expense Tracker

Aplikasi pengelolaan keuangan pribadi yang fleksibel, aman, dan mudah digunakan dengan dua antarmuka terintegrasi: **CLI interaktif (Terminal)** dan **Telegram Bot**, berbagi satu database **SQLite** terpusat.

Arsitektur aplikasi dirancang berlapis secara ketat (*Interface → Services → Database/Models*), tanpa SQL mentah di antarmuka, perhitungan saldo berbasis query agregat murni tanpa floating-point, dan seluruh alur telah diverifikasi dengan pengujian otomatis (*automated testing*).

---

## Daftar Fitur

### 1. Transaksi Keuangan & Rekening
- **Pencatatan Pemasukan & Pengeluaran**: Mendukung kategori sistem dan kustom, metode pembayaran, tanggal transaksi, nominal, dan keterangan opsional.
- **Transfer Antar Rekening/Metode**: Memindahkan saldo antar metode pembayaran (misal: Bank ke Cash) tanpa mengubah total saldo keseluruhan dan tanpa tercatat sebagai pemasukan/pengeluaran di laporan.
- **Biaya Admin Transfer**: Transfer mendukung opsi biaya admin yang otomatis dicatat sebagai transaksi pengeluaran terpisah pada kategori sistem "Biaya Admin" dan terhubung erat (`related_transaction_id`) dengan transfer induknya.
- **Saldo Awal (Opening Balance)**: Ditetapkan per metode pembayaran beserta tanggal efektif berlakunya. Transaksi bertanggal sebelum saldo awal disimpan tetapi tidak memengaruhi saldo.
- **Koreksi Saldo (Adjustment)**: Menyesuaikan saldo riil rekening dengan saldo tercatat secara otomatis. Menghasilkan transaksi penyesuaian (bisa positif/negatif) yang memengaruhi saldo namun tidak dihitung pada laporan arus kas.

### 2. Peringatan Cerdas (Non-Blocking Warnings)
Operasi tetap berhasil dieksekusi dan disimpan, namun pengguna mendapatkan pemberitahuan visual apabila:
1. **Saldo Minus**: Pengeluaran atau transfer menyebabkan saldo suatu metode pembayaran menjadi negatif.
2. **Sebelum Saldo Awal**: Transaksi dicatat pada tanggal sebelum tanggal efektif saldo awal metode tersebut.
3. **Koreksi Saldo Baru**: Transaksi masa lampau diedit atau dihapus padahal terdapat transaksi koreksi saldo (*adjustment*) yang lebih baru pada metode pembayaran tersebut (mengingatkan bahwa koreksi tersebut mungkin perlu ditinjau ulang).

### 3. Integritas & Konsistensi Data
- **Perintah Cek Konsistensi Saldo**: Membandingkan hasil formula agregat database (`compute_balance`) dengan perhitungan mutasi baris-demi-baris dari data mentah (*raw ledger*). Tersedia di CLI (menu 15) dan Bot Telegram (`/konsistensi` atau `/cek_saldo`).
- **Soft Delete & Restore**: Transaksi yang dihapus diberi tanda `deleted` sehingga riwayat tetap terlacak dan dapat dipulihkan (*restore*) kapan saja. Menghapus atau memulihkan transfer secara otomatis mengikutsertakan transaksi biaya adminnya.

### 4. Analisis & Laporan Keuangan
- **Dashboard Interaktif**: Menampilkan saldo per metode pembayaran, saldo total, total pemasukan dan pengeluaran bulan berjalan, jumlah transaksi, serta progress penggunaan anggaran.
- **Laporan Periode**: Laporan mingguan (Senin s.d. Minggu) dan bulanan berisi ringkasan pemasukan, pengeluaran, surplus/defisit, rincian pengeluaran per kategori, dan transaksi terbesar.
- **Grafik Keuangan (Matplotlib PNG)**: Pembuatan 4 grafik beresolusi tinggi yang disimpan ke `reports_output/` dan dapat dikirim langsung oleh bot Telegram:
  1. Pengeluaran per Kategori (*Pie/Donut/Bar Chart*)
  2. Pemasukan per Kategori
  3. Pemasukan vs Pengeluaran per Periode (*Bar Chart*)
  4. Perkembangan Saldo Kumulatif (*Line Trend*)

### 5. Manajemen Anggaran (Budgeting)
- Mengatur batas anggaran bulanan secara global maupun per kategori pengeluaran.
- Evaluasi otomatis dengan peringatan di dua ambang batas:
  - $\ge 80\%$ : Mendekati batas anggaran (*Warning*).
  - $\ge 100\%$ : Melebihi batas anggaran (*Danger*).

### 6. Antarmuka Telegram Bot (Async & Interactive)
- Berbasis `python-telegram-bot` v22+ (asynchronous) dengan *Reply Keyboard* dan *Inline Keyboard*.
- Alur percakapan langkah-demi-langkah (*ConversationHandler*) untuk menambah transaksi, transfer, dan anggaran.
- Perintah Cepat (*Quick Commands*):
  - `/saldo` : Cek rincian saldo per rekening dan totalnya.
  - `/hari_ini` : Ringkasan dan daftar transaksi hari ini.
  - `/riwayat` : Menampilkan 10 transaksi terakhir.
  - `/laporan` : Ringkasan laporan mingguan atau bulanan.
  - `/grafik` : Menghasilkan dan mengirim 4 grafik PNG ke chat.
  - `/budget` : Evaluasi dan monitoring status anggaran.
  - `/konsistensi` : Verifikasi konsistensi saldo agregat vs mutasi mentah.
  - `/batal` : Batalkan form input bertahap kapan saja.
- Keamanan: Whitelist Telegram ID, token aman melalui `.env`, penanganan error global (*global error handler*) agar bot tidak crash diam-diam, dan tidak mencatat data sensitif pengguna ke log.

### 7. File Logging
- Logging otomatis ke konsol (`stderr`) dan file `logs/app.log`.
- Format terstandarisasi dengan pembersihan data sensitif (tidak mencatat token bot, nomor token, nominal transaksi, maupun catatan pribadi ke log).

---

## Batasan Domain & Aturan Aplikasi

1. **Nominal Rupiah Integer**: Semua nominal uang disimpan dan dihitung sebagai bilangan bulat (`int`), tanpa angka desimal/pecahan (*float*) untuk mencegah kesalahan pembulatan mata uang.
2. **Saldo Dinamis**: Saldo tidak disimpan di kolom tabel, melainkan selalu dihitung langsung dari data (*event-sourced*):
   $$\text{Saldo Metode} = \text{Saldo Awal} + \text{Pemasukan} - \text{Pengeluaran} + \text{Transfer Masuk} - \text{Transfer Keluar} + \text{Koreksi}$$
3. **Pemisahan Arus Kas**: Transfer dan Adjustment tidak pernah dihitung sebagai pemasukan atau pengeluaran pada laporan keuangan, dashboard, grafik, maupun perhitungan batas budget.
4. **Format Tanggal & Waktu**: Format tampilan dan input pengguna adalah `DD-MM-YYYY`. Database menyimpan format ISO `YYYY-MM-DD`. Zona waktu standar adalah `Asia/Jakarta` (WIB), dan satu minggu dimulai dari hari Senin.
5. **Autentikasi V1**: Menggunakan sistem Whitelist User ID dari `.env`. Akses di luar whitelist ditolak secara instan.

---

## Persyaratan Sistem

- **Python**: Versi 3.11 atau lebih baru (mendukung Python 3.11 s.d. 3.14).
- **Git**
- **SQLite 3**: Pustaka bawaan Python.
- **Sistem Operasi**: Linux, macOS, atau Windows.

---

## Instalasi

### 1. Clone Repositori
```bash
git clone <url-repository> personal_expense_tracker
cd personal_expense_tracker
```

### 2. Buat dan Aktifkan Virtual Environment
- **Linux / macOS**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- **Windows (PowerShell)**:
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```

### 3. Pasang Dependensi
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Konfigurasi

Salin file template `.env.example` menjadi `.env`:
- **Linux / macOS**: `cp .env.example .env`
- **Windows**: `copy .env.example .env`

Buka file `.env` dan sesuaikan nilainya:

```env
# Token Telegram Bot dari @BotFather
TELEGRAM_BOT_TOKEN=8831148565:AAHQap0uoCQ1BKWXCqyz6bz53L6kU5Hiong

# Daftar ID Pengguna Telegram yang diizinkan (dipisahkan koma)
ALLOWED_TELEGRAM_IDS=7492373440

# Lokasi file database SQLite (default: data/expense_tracker.db)
DB_PATH=data/expense_tracker.db

# Zona waktu (default: Asia/Jakarta)
TIMEZONE=Asia/Jakarta

# Ambang batas peringatan budget (persen)
BUDGET_WARNING_PERCENT=80
BUDGET_EXCEEDED_PERCENT=100

# Level logging (INFO, DEBUG, WARNING, ERROR)
LOG_LEVEL=INFO
```

> **Cara Mendapatkan Token Bot dan ID Telegram:**
> 1. **Token Bot**: Buka Telegram, cari `@BotFather`, kirim `/newbot`, ikuti petunjuk nama bot, lalu salin token API yang diberikan ke `TELEGRAM_BOT_TOKEN`.
> 2. **Telegram ID Anda**: Cari bot `@userinfobot` atau `@raw_data_bot` di Telegram, kirim pesan apa saja, lalu salin angka `id` (misal: `7492373440`) ke `ALLOWED_TELEGRAM_IDS`.

---

## Database & Migrasi (Alembic)

Skema database dikelola penuh oleh **Alembic**.

Saat pertama kali `main.py` dijalankan, aplikasi otomatis menerapkan migrasi terbaru ke head dan mengisi data awal (*seed defaults*: akun default, metode Cash/Bank/E-Wallet, serta kategori default).

Jika ingin menjalankan migrasi secara manual:
```bash
# Terapkan migrasi terbaru
alembic upgrade head

# Cek konsistensi skema model dan revisi migrasi
alembic check
```

---

## Cara Menjalankan Aplikasi

Aplikasi memiliki dua mode yang dapat dijalankan:

### Mode 1: Antarmuka Terminal (CLI)
Jalankan program utama secara interaktif:
```bash
python main.py
```
Menu interaktif akan muncul dengan 15 pilihan menu lengkap mulai dari pencatatan transaksi, transfer, laporan, pembuatan 4 grafik, evaluasi budget, hingga cek konsistensi saldo.

### Mode 2: Telegram Bot
Jalankan bot Telegram dengan argumen `--bot`:
```bash
python main.py --bot
```
Atau melalui modul bot langsung:
```bash
python -m bot
```
Buka bot di aplikasi Telegram Anda dan ketik `/start` untuk mulai mengelola keuangan.

---

## Menjalankan Bot Terus-Menerus (Deployment / Auto-Restart)

Agar Telegram Bot terus berjalan di server VPS (Ubuntu/Debian) dan otomatis menyala kembali jika server restart atau terjadi crash, gunakan layanan **systemd**.

### Langkah-langkah Setup systemd:

1. Buat file service baru di direktori systemd:
   ```bash
   sudo nano /etc/systemd/system/expense-bot.service
   ```

2. Masukkan konfigurasi berikut (sesuaikan username `kaiynan` dan path direktori project):
   ```ini
   [Unit]
   Description=Personal Expense Tracker Telegram Bot
   After=network.target

   [Service]
   Type=simple
   User=kaiynan
   WorkingDirectory=/home/kaiynan/personal_expense_tracker
   ExecStart=/home/kaiynan/personal_expense_tracker/.venv/bin/python main.py --bot
   Restart=always
   RestartSec=10
   Environment=PYTHONUNBUFFERED=1
   EnvironmentFile=/home/kaiynan/personal_expense_tracker/.env

   # Pengaturan batas keamanan
   NoNewPrivileges=true
   PrivateTmp=true

   [Install]
   WantedBy=multi-user.target
   ```

3. Muat ulang konfigurasi systemd dan aktifkan layanannya:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable expense-bot
   sudo systemctl start expense-bot
   ```

4. Periksa status layanan bot:
   ```bash
   sudo systemctl status expense-bot
   ```

5. Memantau log bot secara realtime:
   ```bash
   # Log dari systemd journal
   journalctl -u expense-bot -f

   # Atau langsung dari file log aplikasi
   tail -f logs/app.log
   ```

---

## Deployment & Operasional (Docker & Docker Compose)

Proyek ini siap produksi dengan [`Dockerfile`](Dockerfile) berbasis **Python 3.12-slim**, pengguna non-root (`appuser`), pemeriksaan kesehatan otomatis (`HEALTHCHECK`), dan [`docker-compose.yml`](docker-compose.yml) untuk orkestrasi bot dan CLI.

### 1. Keamanan & Variabel Lingkungan
- **Prinsip Least Privilege**: Container berjalan menggunakan user `appuser:appgroup` tanpa hak akses root.
- **Isolasi Rahasia**: Token bot dan konfigurasi sensitif dipasok **hanya** melalui variabel lingkungan (`.env`), **tidak pernah** dimasukkan ke dalam Docker image (`.dockerignore` mengecualikan `.env`, `.git/`, dan `data/`).
- **Dependensi Terkunci**: Semua dependensi di [`requirements.txt`](requirements.txt) dipin versinya secara eksak (`==`) dan diaudit kerentanannya menggunakan `pip-audit`.

### 2. Langkah-Langkah Deploy
1. **Siapkan Konfigurasi Lingkungan**:
   ```bash
   cp .env.example .env
   # Edit token Telegram dan admin ID
   nano .env
   ```
2. **Bangun Image dan Jalankan Layanan**:
   ```bash
   docker compose up -d --build
   ```
   *Catatan*: Script entrypoint (`docker-entrypoint.sh`) akan secara otomatis:
   - Mencadangkan database SQLite yang ada (`scripts/backup_pre_migration.py`) sebelum migrasi.
   - Menerapkan seluruh migrasi skema terbaru (`alembic upgrade head`).
   - Menjalankan bot dengan auto-restart (`restart: unless-stopped`).

3. **Periksa Status dan Log Container**:
   ```bash
   # Cek status dan kesehatan container (HEALTHCHECK)
   docker compose ps

   # Pantau log Telegram Bot
   docker compose logs -f bot
   ```

4. **Menjalankan CLI Interaktif melalui Docker**:
   ```bash
   docker compose run --rm cli
   ```

5. **Menghentikan Layanan**:
   ```bash
   docker compose down
   ```

---

## Manajemen Backup, Retensi, & Uji Pemulihan (Restore)

Aplikasi memiliki strategi pencadangan berlapis untuk mencegah kehilangan data keuangan:

1. **Backup Pra-Migrasi Otomatis**:
   - Dijalankan otomatis sebelum migrasi skema database dieksekusi (`scripts/backup_pre_migration.py`).
   - Menyimpan cadangan ke `backups/pre_migration_YYYYMMDD_HHMMSS.db` dan memangkas file cadangan lama melebihi kuota 10 file.

2. **Backup Terjadwal & Retensi**:
   - Terintegrasi dengan `JobQueue` Telegram Bot dan skrip CLI (`scripts/backup_retention.py`).
   - Membuat snapshot online yang konsisten menggunakan SQLite Backup API tanpa mengunci database pengguna (`full_backup_YYYYMMDD_HHMMSS.db`).
   - Mempertahankan cadangan selama 14 hari dan selalu melindungi minimal 5 file cadangan terbaru dari penghapusan.

3. **Skrip Uji Pemulihan (Restore Test)**:
   Uji integritas dan kemampuan restore cadangan ke basis data terisolasi tanpa memengaruhi data produksi:
   ```bash
   # Uji file cadangan terbaru
   python scripts/test_restore.py

   # Atau uji file cadangan spesifik (.db, .zip, .json)
   python scripts/test_restore.py --backup backups/full_backup_20261013_120000.db
   ```
   Skrip menjalankan `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, dan verifikasi baris tabel.

---

## Monitoring, Diagnostik, & Alerting Sistem

- **Perintah `/status` Admin**:
  Administrator dapat mengirim `/status` di Telegram untuk melihat:
  - Waktu server & zona waktu pengguna.
  - Ukuran file SQLite, mode jurnal (WAL), dan hasil `PRAGMA integrity_check`.
  - Jumlah total pengguna (aktif, pending, diblokir).
  - Jumlah transaksi keuangan dan cadangan yang tersedia.
  - Jumlah job aktif di `JobQueue`.
- **Notifikasi Error Realtime**:
  Jika terjadi kesalahan tak terduga (*unhandled exception*) saat melayani pengguna:
  - Pengguna menerima pesan penanganan ramah.
  - Seluruh administrator Telegram langsung menerima pesan peringatan (*alert*) berisi waktu, identitas pengguna, perintah pemicu, tipe exception, dan cuplikan *traceback*.

---

## CI/CD, Dependabot, & Pengujian

### 1. GitHub Actions CI & Dependabot
Workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) berjalan pada setiap push & pull request:
- **Linting & Format**: `ruff check .` dan `ruff format --check .`
- **Audit Keamanan**: `pip-audit -r requirements.txt` untuk memverifikasi nol kerentanan dependensi.
- **Rangkaian Pengujian**: `pytest` mencakup seluruh lapisan service, isolasi pengguna, integritas, dan pengujian konkurensi SQLite.
- **Migrasi**: Verifikasi migrasi kosong `alembic upgrade head` dan konsistensi model `alembic check`.
- **Container Build**: Verifikasi pembangunan image Docker container (`docker build`).
- **Dependabot**: Konfigurasi [`.github/dependabot.yml`](.github/dependabot.yml) aktif memantau update dependensi `pip`, `github-actions`, dan `docker`.

### 2. Menjalankan Pengujian Lokal
```bash
# Jalankan seluruh test suite
pytest

# Jalankan pengujian beban konkurensi SQLite secara spesifik
pytest tests/test_concurrency_load.py -v

# Jalankan pengujian operasional V3
pytest tests/test_v3_operational.py -v

# Lakukan audit keamanan paket lokal
pip-audit -r requirements.txt

# Format dan linting kode
ruff check .
ruff format --check .
```

---

## Struktur Direktori

```
personal_expense_tracker/
├── main.py                   # Titik masuk utama aplikasi (CLI dan Bot launcher)
├── config.py                 # Konfigurasi terpusat & pembacaan environment
├── Dockerfile                # Image container Python 3.12-slim, non-root user, HEALTHCHECK
├── docker-compose.yml        # Orkestrasi layanan bot dan cli
├── docker-entrypoint.sh      # Entrypoint: backup pra-migrasi, migrasi, dan startup
├── .dockerignore             # Pengecualian rahasia (.env), git, dan volume data
├── pyproject.toml            # Metadata proyek & konfigurasi alat (Ruff, Pytest)
├── requirements.txt          # Daftar dependensi terkunci (pinned versions)
├── alembic.ini               # Konfigurasi migrasi database Alembic
├── .env.example              # Template variabel lingkungan
├── .github/
│   ├── dependabot.yml        # Konfigurasi Dependabot (pip, actions, docker)
│   └── workflows/ci.yml      # CI workflow: ruff, pytest, alembic, pip-audit, docker build
├── scripts/
│   ├── healthcheck.py        # Pemeriksaan kesehatan database container
│   ├── backup_pre_migration.py # Backup otomatis pra-migrasi dengan pemangkasan
│   ├── backup_retention.py   # Backup berkala online dengan retensi 14 hari
│   └── test_restore.py       # Skrip pengujian pemulihan cadangan terisolasi
├── alembic/                  # Skrip migrasi skema database
├── bot/                      # Modul Telegram Bot (async, handlers, keyboards, auth)
├── charts/                   # Pembuat grafik Matplotlib (Agg backend)
├── cli/                      # Antarmuka Command Line Interface (menu, display, input)
├── database/                 # Engine SQLAlchemy, session_scope, query, agregat balances, seed
├── models/                   # Model tabel ORM SQLAlchemy & relasi
├── reports/                  # Logika agregasi laporan mingguan & bulanan
├── services/                 # Lapisan logika bisnis sinkron (transaksi, transfer, saldo, budget, backup)
├── utils/                    # Validasi input, parsing tanggal, format Rupiah, rate limiter
├── tests/                    # Test suite Pytest (800+ test case komprehensif)
├── data/                     # Lokasi file database SQLite (.db) [volume terisolasi]
├── backups/                  # Folder penyimpanan cadangan database (.db, .zip, .json)
├── logs/                     # File log aplikasi (app.log) [di-gitignore]
└── reports_output/           # Output file grafik chart PNG & laporan PDF/Excel/CSV
```
