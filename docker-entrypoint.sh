#!/bin/sh
set -e

# ==============================================================================
# Entrypoint Docker Container Personal Expense Tracker
# 1. Jalankan backup otomatis database sebelum migrasi jika file database sudah ada
# 2. Jalankan migrasi skema database (alembic upgrade head)
# 3. Jalankan perintah container (default: python main.py --bot)
# ==============================================================================

echo "🚀 [entrypoint] Memulai inisialisasi container..."

# Pastikan folder data, backups, logs, dan reports_output ada
mkdir -p data backups logs reports_output

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
