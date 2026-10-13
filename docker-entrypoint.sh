#!/bin/sh
set -e

# ==============================================================================
# Entrypoint Docker Container Personal Expense Tracker
# 1. Jalankan backup otomatis database sebelum migrasi jika file database sudah ada
# 2. Jalankan migrasi skema database (alembic upgrade head)
# 3. Jalankan perintah container (default: python main.py --bot)
# ==============================================================================

echo "🚀 [entrypoint] Memulai inisialisasi container..."

# Pastikan folder data, backups, logs, dan reports_output ada dan memiliki izin tulis penuh
mkdir -p /app/data /app/backups /app/logs /app/reports_output data backups logs reports_output
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

echo "✨ [entrypoint] Inisialisasi selesai. Menjalankan perintah utama: $@"
exec "$@"
