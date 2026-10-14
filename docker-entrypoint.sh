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
mkdir -p /app/data /app/backups /app/logs /app/reports_output data backups logs reports_output 2>/dev/null || true

# 1. Backup otomatis database sebelum migrasi
echo "💾 [entrypoint] Menjalankan pencadangan pra-migrasi..."
python scripts/backup_pre_migration.py || {
    echo "⚠️ [entrypoint] Peringatan: Backup pra-migrasi mengalami kendala, melanjutkan migrasi..."
}

# 2. Eksekusi migrasi skema database ke revisi terbaru
echo "🔄 [entrypoint] Menerapkan migrasi skema database (alembic upgrade head)..."
alembic upgrade head

# 3. Jika berjalan di lingkungan Railway / Cloud di mana $PORT disediakan secara dinamis,
# jalankan uvicorn yang mengikat ke $PORT dan menyalakan Web + Telegram Bot sekaligus lewat lifespan
if [ -n "$PORT" ]; then
    echo "🌐 [entrypoint] Lingkungan cloud Railway terdeteksi (PORT=$PORT). Menjalankan uvicorn Web Dashboard & Telegram Bot..."
    exec uvicorn web.app:app --host 0.0.0.0 --port "$PORT"
fi

echo "✨ [entrypoint] Inisialisasi selesai. Menjalankan perintah utama: $@"
exec "$@"
