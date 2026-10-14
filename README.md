# Personal Expense Tracker

Aplikasi pengelolaan keuangan pribadi yang fleksibel, aman, dan mudah digunakan dengan tiga antarmuka terintegrasi: **Web Dashboard (Modern Fintech)**, **Telegram Bot**, dan **CLI interaktif (Terminal)**, berbagi satu database **SQLite** terpusat.

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
- **Perintah Cek Konsistensi Saldo**: Membandingkan hasil formula agregat database (`compute_balance`) dengan perhitungan mutasi baris-demi-baris dari data mentah (*raw ledger*). Tersedia di Web Dashboard, Bot Telegram (`/konsistensi`), dan CLI.
- **Soft Delete & Restore**: Transaksi yang dihapus diberi tanda `deleted` sehingga riwayat tetap terlacak dan dapat dipulihkan (*restore*) kapan saja. Menghapus atau memulihkan transfer secara otomatis mengikutsertakan transaksi biaya adminnya.

### 4. Analisis & Laporan Keuangan
- **Dashboard Interaktif**: Menampilkan saldo per metode pembayaran, saldo total, total pemasukan dan pengeluaran bulan berjalan, jumlah transaksi, serta progress penggunaan anggaran.
- **Laporan Periode**: Laporan harian, mingguan (Senin s.d. Minggu), bulanan, dan perbandingan periode berisi ringkasan pemasukan, pengeluaran, surplus/defisit, rincian per kategori, dan transaksi terbesar.
- **Ekspor Multi-Format**: Unduh laporan dalam format **CSV**, **Excel (.xlsx)**, dan **PDF** lengkap dengan perlindungan formula injection untuk sel yang berawalan `=`, `+`, `-`, atau `@`.
- **Grafik Keuangan**:
  - **Web Dashboard**: 4 visualisasi interaktif via Chart.js (Donut Pengeluaran, Donut Pemasukan, Bar Arus Kas, Line Tren Saldo).
  - **Telegram Bot**: 4 grafik beresolusi tinggi (Matplotlib PNG) dikirim langsung ke ruang chat.

### 5. Manajemen Anggaran (Budgeting) & Transaksi Rutin
- Mengatur batas anggaran bulanan global maupun per kategori pengeluaran, anggaran mingguan, dan rollover sisa budget.
- Evaluasi otomatis dengan indikator visual di dua ambang batas:
  - $\ge 80\%$ : Mendekati batas anggaran (*Warning*).
  - $\ge 100\%$ : Melebihi batas anggaran (*Danger*).
- **Transaksi Rutin (Recurring)**: Penjadwalan pengeluaran dan pemasukan berulang (harian, mingguan, bulanan) dengan eksekusi otomatis.

### 6. Antarmuka Web Dashboard (FastAPI, Tailwind CSS, HTMX, Chart.js)
- **App Shell Fintech Modern**: Desain responsif (Desktop Sidebar, Mobile Bottom Bar), dukungan Light/Dark mode, dan sensor **Privacy Mode (👁️)** untuk menyembunyikan nominal saldo di tempat umum.
- **Autentikasi OTP Telegram**: Pengguna login dengan memasukkan `telegram_id` dan memverifikasi kode OTP 6 digit yang dikirimkan secara privat oleh Telegram Bot (tanpa password, masa berlaku 5 menit, rate limit).
- **Quick-Action Bar**: Input cepat transaksi di bagian atas dashboard (misal: `makan 15000 gopay`) dengan pratinjau konfirmasi HTMX instan.
- **Modal Desktop & Drawer Bottom Sheet Mobile (`+`)**: Form pencatatan lengkap dengan tab Pengeluaran, Pemasukan, Transfer (dengan input biaya admin), dan Input Cepat.
- **Live Rupiah Formatter**: Format angka otomatis saat mengetik (misal: `Rp15.000`) dengan batas aman hingga Rp10 miliar.
- **Riwayat Transaksi Dinamis**: Filter jenis, kategori, metode, pencarian teks, rentang tanggal, pagination, serta aksi edit dan hapus tanpa reload halaman.
- **Impor Mutasi CSV**: Wizard impor rekening dengan deteksi duplikat otomatis dan konfirmasi aturan kategori sebelum disimpan ke database.
- **Panel Khusus Administrator (`/admin`)**:
  - Dashboard status operasional sistem & diagnostik SQLite (path, ukuran, integritas, mode WAL, backup terbaru).
  - Manajemen akun pengguna: persetujuan pendaftaran (*approve*) dan pemblokiran (*block*).
  - Manajemen kode undangan (*invites*): pembuatan kode kustom/acak dan batas kedaluwarsa.
  - Unduh cadangan sistem penuh (*full admin backup ZIP*).

### 7. Antarmuka Telegram Bot (Async & Interactive)
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
  - `/status` : Status operasional database & server (khusus admin).
  - `/batal` : Batalkan form input bertahap kapan saja.
- Keamanan: Whitelist Telegram ID, token aman melalui `.env`, penanganan error global (*global error handler*) agar bot tidak crash diam-diam, dan sanitasi log tanpa data finansial sensitif.

### 8. File Logging & Penanganan Error
- Logging otomatis ke konsol (`stderr`) dan file `logs/app.log`.
- Format terstandarisasi dengan pembersihan data sensitif (tidak pernah mencatat token bot, kode OTP, nominal transaksi, maupun catatan pribadi ke log).
- Global exception handler di FastAPI yang menyajikan halaman error ramah pengguna (403, 404, 500) dan toast HTMX tanpa mengekspos informasi internal sistem.

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

# Kunci rahasia signed session cookie Web Dashboard (wajib diisi acak di production)
SECRET_KEY=ganti-dengan-kunci-rahasia-panjang-dan-acak-di-production
```

> **Cara Mendapatkan Token Bot dan ID Telegram:**
> 1. **Token Bot**: Buka Telegram, cari `@BotFather`, kirim `/newbot`, ikuti petunjuk nama bot, lalu salin token API yang diberikan ke `TELEGRAM_BOT_TOKEN`.
> 2. **Telegram ID Anda**: Cari bot `@userinfobot` atau `@raw_data_bot` di Telegram, kirim pesan apa saja, lalu salin angka `id` (misal: `7492373440`) ke `ALLOWED_TELEGRAM_IDS`.

---

## Database & Migrasi (Alembic)

Skema database dikelola penuh oleh **Alembic**.

Saat pertama kali aplikasi dijalankan, sistem otomatis menerapkan migrasi terbaru ke head dan mengisi data awal (*seed defaults*: akun default, metode Cash/Bank/E-Wallet, serta kategori default).

Jika ingin menjalankan migrasi secara manual:
```bash
# Terapkan migrasi terbaru
alembic upgrade head

# Cek konsistensi skema model dan revisi migrasi
alembic check
```

---

## Cara Menjalankan Aplikasi

Aplikasi memiliki tiga mode pengoperasian yang dapat dipilih sesuai kebutuhan:

### Mode 1: Web Dashboard (+ Telegram Bot Bersamaan) — *Rekomendasi*
Jalankan server web yang secara otomatis menyalakan **Web Dashboard** dan **Telegram Bot** (+ JobQueue) dalam satu proses event loop Python yang sama (`FastAPI lifespan`):
```bash
python main.py --web
```
Atau menggunakan `uvicorn` langsung:
```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```
Buka peramban (browser) di `http://localhost:8000`:
1. Masukkan angka `telegram_id` Anda yang telah terdaftar/aktif.
2. Bot Telegram akan mengirimkan kode OTP 6 digit.
3. Masukkan kode OTP untuk masuk ke Dashboard Keuangan Anda!

### Mode 2: Telegram Bot Saja
Jalankan hanya antarmuka bot Telegram dengan argumen `--bot`:
```bash
python main.py --bot
```
Atau melalui modul bot langsung:
```bash
python -m bot
```
Buka bot di aplikasi Telegram Anda dan ketik `/start` untuk mulai mengelola keuangan.

### Mode 3: Antarmuka Terminal (CLI)
Jalankan program utama secara interaktif di terminal:
```bash
python main.py
```
Menu interaktif akan muncul dengan 15 pilihan menu lengkap mulai dari pencatatan transaksi, transfer, laporan, pembuatan grafik, evaluasi budget, hingga audit saldo.

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
### 3. Deployment Railway (Satu Container / Proses Tunggal)

Aplikasi dirancang agar dapat di-deploy ke **Railway** dalam **satu container tunggal** yang menjalankan **Web Dashboard** dan **Telegram Bot** (+ JobQueue) secara berdampingan tanpa memerlukan dua container terpisah:

1. **Persistent Volume**:
   - Hubungkan Railway Persistent Volume ke path `/app/data` agar database SQLite tetap persisten saat container me-restart atau me-redeploy.
2. **Variabel Lingkungan di Railway Dashboard**:
   - `TELEGRAM_BOT_TOKEN`: Token bot Anda dari @BotFather.
   - `ALLOWED_TELEGRAM_IDS`: ID Telegram pengguna yang diizinkan (dipisahkan koma).
   - `SECRET_KEY`: Kunci acak panjang untuk menandatangani cookie sesi web (misal hasil dari `openssl rand -hex 32`).
   - `DB_PATH`: `/app/data/expense_tracker.db`
   - `TZ`: `Asia/Jakarta`
   - *Catatan*: Variabel `$PORT` disediakan otomatis oleh platform Railway. Server web akan mendeteksi dan mengikat ke port tersebut secara dinamis.
3. **Mekanisme Startup Otomatis**:
   - Saat container boot, `docker-entrypoint.sh` secara otomatis mengeksekusi backup pra-migrasi (`scripts/backup_pre_migration.py`) dan menerapkan migrasi skema database (`alembic upgrade head`).
   - Perintah default container `python main.py --web` mengaktifkan `FastAPI lifespan`, yang menyalakan Telegram Bot dan Web Dashboard pada event loop Python yang sama.
   - Pemeriksaan kesehatan (`HEALTHCHECK`) memantau kesiapan endpoint `/health` secara berkala.

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

- **Perintah `/status` Bot & Panel Admin Web (`/admin`)**:
  Administrator dapat memantau kesehatan operasional secara realtime:
  - Waktu server & zona waktu pengguna.
  - Ukuran file SQLite, mode jurnal (WAL), dan hasil `PRAGMA integrity_check`.
  - Jumlah total pengguna (aktif, pending, diblokir).
  - Jumlah transaksi keuangan dan cadangan yang tersedia.
  - Unduh cadangan sistem penuh (*full admin backup ZIP*).
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
# Jalankan seluruh test suite (950+ tests)
pytest

# Jalankan pengujian beban konkurensi SQLite secara spesifik
pytest tests/test_concurrency_load.py -v

# Jalankan pengujian web dashboard
pytest tests/test_web_admin.py tests/test_web_w4.py -v

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
├── main.py                   # Titik masuk utama aplikasi (CLI, Bot, dan Web launcher)
├── config.py                 # Konfigurasi terpusat & pembacaan environment
├── Dockerfile                # Image container Python 3.12-slim, non-root user, HEALTHCHECK /health
├── docker-compose.yml        # Orkestrasi layanan bot dan cli
├── docker-entrypoint.sh      # Entrypoint: backup pra-migrasi, migrasi, dan startup web/bot
├── .dockerignore             # Pengecualian rahasia (.env), git, dan volume data
├── pyproject.toml            # Metadata proyek & konfigurasi alat (Ruff, Pytest)
├── requirements.txt          # Daftar dependensi terkunci (pinned versions)
├── alembic.ini               # Konfigurasi migrasi database Alembic
├── .env.example              # Template variabel lingkungan
├── .github/
│   ├── dependabot.yml        # Konfigurasi Dependabot (pip, actions, docker)
│   └── workflows/ci.yml      # CI workflow: ruff, pytest, alembic, pip-audit, docker build
├── scripts/
│   ├── healthcheck.py        # Pemeriksaan kesehatan endpoint /health & database SQLite
│   ├── backup_pre_migration.py # Backup otomatis pra-migrasi dengan pemangkasan
│   ├── backup_retention.py   # Backup berkala online dengan retensi 14 hari
│   └── test_restore.py       # Skrip pengujian pemulihan cadangan terisolasi
├── web/                      # Aplikasi FastAPI Web Dashboard (Fase W)
│   ├── app.py                # App factory, lifespan Telegram Bot, session middleware, error handlers
│   ├── deps.py               # Dependency injection (session factory, require_user, require_admin)
│   ├── routes/               # Route handlers (auth, dashboard, charts, tx, budgets, admin, dll.)
│   └── templates/            # Template Jinja2 (Tailwind CSS, HTMX, Chart.js via CDN)
├── alembic/                  # Skrip migrasi skema database
├── bot/                      # Modul Telegram Bot (async, handlers, keyboards, auth)
├── charts/                   # Pembuat grafik Matplotlib (Agg backend)
├── cli/                      # Antarmuka Command Line Interface (menu, display, input)
├── database/                 # Engine SQLAlchemy, session_scope, query, agregat balances, seed
├── models/                   # Model tabel ORM SQLAlchemy & relasi
├── reports/                  # Logika agregasi laporan mingguan & bulanan
├── services/                 # Lapisan logika bisnis sinkron (transaksi, transfer, saldo, budget, backup)
├── utils/                    # Validasi input, parsing tanggal, format Rupiah, rate limiter
├── tests/                    # Test suite Pytest (950+ test case komprehensif)
├── data/                     # Lokasi file database SQLite (.db) [volume terisolasi]
├── backups/                  # Folder penyimpanan cadangan database (.db, .zip, .json)
├── logs/                     # File log aplikasi (app.log) [di-gitignore]
└── reports_output/           # Output file grafik chart PNG & laporan PDF/Excel/CSV
```
