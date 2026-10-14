# Gunakan base image Python 3.12 slim yang stabil dan ringan untuk efisiensi resource
FROM python:3.12-slim

# Variabel lingkungan standar Python & Matplotlib headless
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/app \
    TZ=Asia/Jakarta \
    PORT=8000

WORKDIR /app

# Buat pengguna non-root demi keamanan dan kepatuhan prinsip least privilege
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home appuser

# Salin dan pasang dependensi terlebih dahulu agar pemanfaatan layer cache optimal
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Salin seluruh kode aplikasi dan skrip
COPY . .

# Pastikan direktori runtime tersedia, entrypoint executable, dan izin volume penuh
RUN mkdir -p /app/data /app/reports_output /app/backups /app/logs && \
    chmod -R 777 /app/data /app/reports_output /app/backups /app/logs && \
    chmod +x /app/docker-entrypoint.sh && \
    chown -R appuser:appgroup /app

# Port standar yang diekspos untuk Web Dashboard
EXPOSE 8000

# Deklarasi pengguna non-root demi kepatuhan keamanan
USER appuser
# Eksekusi entrypoint sebagai root agar izin persistent volume cloud (Railway) dapat disesuaikan otomatis
USER root

# Healthcheck berkala untuk memastikan endpoint /health dan database siap melayani
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python scripts/healthcheck.py || exit 1

# Entrypoint otomatis menjalankan backup pra-migrasi dan alembic upgrade head
ENTRYPOINT ["./docker-entrypoint.sh"]

# Default command: jalankan Telegram Bot (atau Web Dashboard jika $PORT Railway disetel)
CMD ["python", "main.py", "--bot"]
