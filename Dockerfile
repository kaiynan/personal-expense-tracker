# Gunakan base image Python 3.12 slim yang stabil dan ringan untuk efisiensi resource
FROM python:3.12-slim

# Variabel lingkungan standar Python & Matplotlib headless
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    TZ=Asia/Jakarta

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

# Pastikan direktori runtime tersedia, entrypoint executable, dan dimiliki oleh appuser
RUN mkdir -p /app/data /app/reports_output /app/backups /app/logs && \
    chmod +x /app/docker-entrypoint.sh && \
    chown -R appuser:appgroup /app

USER appuser

# Healthcheck berkala untuk memastikan database dan sistem siap melayani
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python scripts/healthcheck.py || exit 1

# Entrypoint otomatis menjalankan backup pra-migrasi dan alembic upgrade head
ENTRYPOINT ["./docker-entrypoint.sh"]

# Default command: jalankan Telegram Bot
CMD ["python", "main.py", "--bot"]
