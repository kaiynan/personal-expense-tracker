#!/bin/sh
set -e

# ==============================================================================
# Entrypoint Docker Container Personal Expense Tracker
# 1. Jalankan backup otomatis database sebelum migrasi jika file database sudah ada
# 2. Jalankan migrasi skema database (alembic upgrade head)
# 3. Jalankan server uvicorn yang mengikat ke $PORT Railway (Web + Bot via lifespan)
#    atau jalankan perintah container ($@)
# ==============================================================================

echo "🚀 [entrypoint] Memulai inisialisasi container..."

# Pastikan folder data, backups, logs, dan reports_output ada dan memiliki izin tulis penuh
# (Penting untuk persistent volume cloud Railway yang di-mount sebagai root)
mkdir -p /app/data /app/backups /app/logs /app/reports_output data backups logs reports_output 2>/dev/null || true
chmod -R 777 /app/data /app/backups /app/logs /app/reports_output data backups logs reports_output 2>/dev/null || true
chown -R appuser:appgroup /app/data /app/backups /app/logs /app/reports_output data backups logs reports_output 2>/dev/null || true

# 1. Backup otomatis database sebelum migrasi
echo "💾 [entrypoint] Menjalankan pencadangan pra-migrasi..."
python scripts/backup_pre_migration.py || {
    echo "⚠️ [entrypoint] Peringatan: Backup pra-migrasi mengalami kendala, melanjutkan migrasi..."
}

# 2. Eksekusi migrasi skema database ke revisi terbaru
echo "🔄 [entrypoint] Menerapkan migrasi skema database (alembic upgrade head)..."
alembic upgrade head

# 3. Tentukan port eksekusi (Railway $PORT atau fallback 8000)
PORT="${PORT:-8000}"

# 4. Jalankan uvicorn Web Dashboard (+ Telegram Bot via lifespan) jika:
# - Tidak ada argumen ($# -eq 0)
# - Argumen adalah "web" atau "python main.py --web"
# - Argumen default CMD container: "python main.py --bot"
# - Berada di lingkungan cloud Railway (RAILWAY_ENVIRONMENT / RAILWAY_PROJECT_ID / RAILWAY_STATIC_URL)
if [ "$#" -eq 0 ] || [ "$1" = "web" ] || [ "$*" = "python main.py --bot" ] || [ "$*" = "python main.py --web" ] || [ -n "$RAILWAY_ENVIRONMENT" ] || [ -n "$RAILWAY_PROJECT_ID" ] || [ -n "$RAILWAY_STATIC_URL" ]; then
    echo "🌐 [entrypoint] Menjalankan uvicorn Web Dashboard & Telegram Bot pada 0.0.0.0:$PORT..."
    exec uvicorn web.app:app --host 0.0.0.0 --port "$PORT"
fi

echo "✨ [entrypoint] Inisialisasi selesai. Menjalankan perintah utama: $@"
exec "$@"
