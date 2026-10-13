"""Pengujian konfigurasi Docker, docker-compose, CI, dan Dependabot."""

from __future__ import annotations

import config


def test_dockerfile_exists_and_follows_best_practices():
    """Dockerfile tersedia, memakai Python 3.12-slim, non-root user, HEALTHCHECK, dan entrypoint."""
    dockerfile_path = config.BASE_DIR / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile harus ada di root proyek"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in content
    assert "PYTHONUNBUFFERED=1" in content
    assert "MPLBACKEND=Agg" in content
    assert "TZ=Asia/Jakarta" in content
    assert "WORKDIR /app" in content
    assert "groupadd --system appgroup" in content
    assert "useradd --system --gid appgroup" in content
    assert "USER appuser" in content
    assert "COPY requirements.txt" in content
    assert "pip install --no-cache-dir -r requirements.txt" in content
    assert "HEALTHCHECK" in content
    assert "scripts/healthcheck.py" in content
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in content
    assert 'CMD ["python", "main.py", "--bot"]' in content


def test_dockerignore_excludes_secrets_and_runtime_artifacts():
    """.dockerignore mengecualikan .env, .git, virtualenv, dan folder data/output lokal."""
    dockerignore_path = config.BASE_DIR / ".dockerignore"
    assert dockerignore_path.is_file(), ".dockerignore harus ada di root proyek"

    lines = {
        line.strip()
        for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert ".env" in lines
    assert ".git/" in lines
    assert ".venv/" in lines
    assert "data/" in lines
    assert "logs/" in lines
    assert "reports_output/" in lines
    assert "backups/" in lines
    assert "__pycache__/" in lines


def test_docker_compose_defines_bot_and_cli_with_shared_volumes():
    """docker-compose.yml mendefinisikan layanan bot dan cli dengan volume SQLite bersama."""
    compose_path = config.BASE_DIR / "docker-compose.yml"
    assert compose_path.is_file(), "docker-compose.yml harus ada di root proyek"

    content = compose_path.read_text(encoding="utf-8")
    assert "services:" in content
    assert "  bot:" in content
    assert "  cli:" in content
    assert "restart: unless-stopped" in content
    assert "./data:/app/data" in content
    assert "./reports_output:/app/reports_output" in content
    assert "./backups:/app/backups" in content
    assert "./logs:/app/logs" in content
    assert 'command: ["python", "main.py", "--bot"]' in content
    assert 'command: ["python", "main.py"]' in content
    assert "stdin_open: true" in content
    assert "tty: true" in content


def test_docker_entrypoint_script_exists_and_runs_pre_migration_backup():
    """docker-entrypoint.sh memanggil backup pra-migrasi dan alembic upgrade head."""
    entrypoint_path = config.BASE_DIR / "docker-entrypoint.sh"
    assert entrypoint_path.is_file(), "docker-entrypoint.sh harus tersedia di root"

    content = entrypoint_path.read_text(encoding="utf-8")
    assert "scripts/backup_pre_migration.py" in content
    assert "alembic upgrade head" in content
    assert 'exec "$@"' in content


def test_github_actions_ci_includes_lint_pytest_alembic_audit_and_docker_build():
    """Workflow GitHub Actions mencakup ruff, pytest, alembic, pip-audit, dan docker build."""
    ci_path = config.BASE_DIR / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), ".github/workflows/ci.yml harus tersedia"

    content = ci_path.read_text(encoding="utf-8")
    assert "ruff check ." in content
    assert "ruff format --check ." in content
    assert "pytest" in content
    assert "alembic upgrade head" in content
    assert "alembic check" in content
    assert "pip-audit" in content
    assert "docker build" in content


def test_dependabot_configuration_file():
    """.github/dependabot.yml mengonfigurasi update pip, github-actions, dan docker."""
    dep_path = config.BASE_DIR / ".github" / "dependabot.yml"
    assert dep_path.is_file(), ".github/dependabot.yml harus tersedia"

    content = dep_path.read_text(encoding="utf-8")
    assert 'package-ecosystem: "pip"' in content
    assert 'package-ecosystem: "github-actions"' in content
    assert 'package-ecosystem: "docker"' in content
