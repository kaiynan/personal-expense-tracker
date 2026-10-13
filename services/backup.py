"""Service backup dan restore: ekspor, validasi, dan pemulihan data keuangan pengguna.

Semua fungsi sinkron, menerima `Session` dan `user_id`, dan tidak melakukan commit
agar pemanggil dapat mengaturnya di dalam `session_scope`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

import config
from database import queries
from models import (
    Budget,
    Category,
    OpeningBalance,
    PaymentMethod,
    QuickAlias,
    RecurringTransaction,
    ReminderSetting,
    Transaction,
)
from reports.export import sanitize_cell_value
from services.common import require_admin, require_user
from utils.errors import NotFoundError, ValidationError


def _calculate_checksum(data: Any) -> str:
    """Hitung SHA256 dari payload data backup (deterministik berurut)."""
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def create_backup_data(session: Session, user_id: int) -> dict[str, Any]:
    """Hasilkan kamus data cadangan lengkap milik `user_id`."""
    user = require_user(session, user_id)
    raw = queries.get_all_user_data(session, user_id)

    pm_list = [
        {
            "id": m.id,
            "name": m.name,
            "is_archived": m.is_archived,
        }
        for m in raw["payment_methods"]
    ]

    cat_list = [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "is_archived": c.is_archived,
            "is_system": c.is_system,
        }
        for c in raw["categories"]
    ]

    ob_list = [
        {
            "id": ob.id,
            "method_id": ob.method_id,
            "amount": ob.amount,
            "effective_date": ob.effective_date.isoformat(),
        }
        for ob in raw["opening_balances"]
    ]

    b_list = [
        {
            "id": b.id,
            "category_id": b.category_id,
            "month": b.month,
            "amount": b.amount,
        }
        for b in raw["budgets"]
    ]

    rec_list = [
        {
            "id": r.id,
            "name": r.name,
            "type": r.type,
            "amount": r.amount,
            "category_id": r.category_id,
            "method_id": r.method_id,
            "frequency": r.frequency,
            "day_of_month": r.day_of_month,
            "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "last_generated_date": (
                r.last_generated_date.isoformat() if r.last_generated_date else None
            ),
            "note": r.note,
            "is_active": r.is_active,
        }
        for r in raw["recurring"]
    ]

    tx_list = [
        {
            "id": t.id,
            "type": t.type,
            "amount": t.amount,
            "date": t.date.isoformat(),
            "category_id": t.category_id,
            "method_id": t.method_id,
            "to_method_id": t.to_method_id,
            "related_transaction_id": t.related_transaction_id,
            "note": t.note,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in raw["transactions"]
    ]

    qa_list = [
        {
            "id": qa.id,
            "alias": qa.alias,
            "target_type": qa.target_type,
            "category_id": qa.category_id,
            "method_id": qa.method_id,
        }
        for qa in raw.get("quick_aliases", [])
    ]

    rs_list = [
        {
            "id": rs.id,
            "is_daily_active": rs.is_daily_active,
            "daily_time": rs.daily_time,
            "is_weekly_active": rs.is_weekly_active,
            "is_monthly_active": rs.is_monthly_active,
            "last_daily_sent": (rs.last_daily_sent.isoformat() if rs.last_daily_sent else None),
            "last_weekly_sent": (rs.last_weekly_sent.isoformat() if rs.last_weekly_sent else None),
            "last_monthly_sent": (
                rs.last_monthly_sent.isoformat() if rs.last_monthly_sent else None
            ),
        }
        for rs in raw.get("reminder_settings", [])
    ]

    data = {
        "payment_methods": pm_list,
        "categories": cat_list,
        "opening_balances": ob_list,
        "budgets": b_list,
        "recurring": rec_list,
        "transactions": tx_list,
        "quick_aliases": qa_list,
        "reminder_settings": rs_list,
    }

    checksum = _calculate_checksum(data)
    now_iso = datetime.now(UTC).isoformat()

    return {
        "version": "1.0",
        "app": config.APP_NAME,
        "created_at": now_iso,
        "user_id": user_id,
        "user_name": user.name,
        "checksum": checksum,
        "summary": {
            "payment_methods": len(pm_list),
            "categories": len(cat_list),
            "opening_balances": len(ob_list),
            "budgets": len(b_list),
            "recurring": len(rec_list),
            "transactions": len(tx_list),
            "quick_aliases": len(qa_list),
            "reminder_settings": len(rs_list),
        },
        "data": data,
    }


def export_backup(
    session: Session,
    user_id: int,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor data milik `user_id` ke file JSON cadangan dan kembalikan lokasinya."""
    backup_dict = create_backup_data(session, user_id)

    if file_path is None:
        config.ensure_directories()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = config.BACKUPS_DIR / f"backup_user_{user_id}_{timestamp}.json"
    else:
        target_path = Path(file_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as f:
        json.dump(backup_dict, f, indent=2, ensure_ascii=False)

    return target_path


def export_user_backup_zip(
    session: Session,
    user_id: int,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor data lengkap pengguna ke file arsip ZIP berisi backup.json dan file CSV."""
    user = require_user(session, user_id)
    backup_dict = create_backup_data(session, user_id)
    data = backup_dict["data"]

    if file_path is None:
        config.ensure_directories()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = config.BACKUPS_DIR / f"backup_user_{user_id}_{timestamp}.zip"
    else:
        target_path = Path(file_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. transactions.csv
    tx_buf = io.StringIO()
    tx_writer = csv.writer(tx_buf)
    tx_writer.writerow(
        [
            "id",
            "type",
            "amount",
            "date",
            "category_id",
            "method_id",
            "to_method_id",
            "note",
            "status",
        ]
    )
    for t in data["transactions"]:
        tx_writer.writerow(
            [
                t.get("id"),
                sanitize_cell_value(t.get("type")),
                t.get("amount"),
                sanitize_cell_value(t.get("date")),
                t.get("category_id"),
                t.get("method_id"),
                t.get("to_method_id"),
                sanitize_cell_value(t.get("note") or ""),
                sanitize_cell_value(t.get("status")),
            ]
        )

    # 2. categories.csv
    cat_buf = io.StringIO()
    cat_writer = csv.writer(cat_buf)
    cat_writer.writerow(["id", "name", "type", "is_archived", "is_system"])
    for c in data["categories"]:
        cat_writer.writerow(
            [
                c.get("id"),
                sanitize_cell_value(c.get("name")),
                sanitize_cell_value(c.get("type")),
                c.get("is_archived"),
                c.get("is_system"),
            ]
        )

    # 3. payment_methods.csv
    pm_buf = io.StringIO()
    pm_writer = csv.writer(pm_buf)
    pm_writer.writerow(["id", "name", "is_archived"])
    for m in data["payment_methods"]:
        pm_writer.writerow([m.get("id"), sanitize_cell_value(m.get("name")), m.get("is_archived")])

    # 4. budgets.csv
    b_buf = io.StringIO()
    b_writer = csv.writer(b_buf)
    b_writer.writerow(["id", "category_id", "month", "amount"])
    for b in data["budgets"]:
        b_writer.writerow([b.get("id"), b.get("category_id"), b.get("month"), b.get("amount")])

    manifest = {
        "version": "1.0",
        "app": config.APP_NAME,
        "backup_type": "user_zip",
        "user_id": user_id,
        "user_name": user.name,
        "created_at": backup_dict["created_at"],
        "checksum": backup_dict["checksum"],
        "summary": backup_dict["summary"],
        "files": [
            "backup.json",
            "transactions.csv",
            "categories.csv",
            "payment_methods.csv",
            "budgets.csv",
        ],
    }

    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.json", json.dumps(backup_dict, indent=2, ensure_ascii=False))
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("transactions.csv", tx_buf.getvalue())
        zf.writestr("categories.csv", cat_buf.getvalue())
        zf.writestr("payment_methods.csv", pm_buf.getvalue())
        zf.writestr("budgets.csv", b_buf.getvalue())

    return target_path


def export_full_admin_backup(
    session: Session,
    user_id: int,
    file_path: str | Path | None = None,
) -> Path:
    """Ekspor cadangan sistem penuh seluruh pengguna (khusus admin)."""
    require_admin(session, user_id)

    if file_path is None:
        config.ensure_directories()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = config.BACKUPS_DIR / f"full_admin_backup_{timestamp}.zip"
    else:
        target_path = Path(file_path)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    all_users = queries.list_users(session)
    all_invites = queries.list_all_invites(session)

    invites_data = [
        {
            "id": i.id,
            "user_id": i.user_id,
            "code": i.code,
            "is_used": i.is_used,
            "used_by_user_id": i.used_by_user_id,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "expires_at": i.expires_at.isoformat() if i.expires_at else None,
            "used_at": i.used_at.isoformat() if i.used_at else None,
        }
        for i in all_invites
    ]

    manifest = {
        "version": "1.0",
        "app": config.APP_NAME,
        "backup_type": "full_system_admin",
        "created_by_admin_id": user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "total_users": len(all_users),
        "total_invites": len(all_invites),
    }

    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("invites.json", json.dumps(invites_data, indent=2, ensure_ascii=False))
        for u in all_users:
            u_backup = create_backup_data(session, u.id)
            zf.writestr(
                f"users/user_{u.id}_{u.name}.json",
                json.dumps(u_backup, indent=2, ensure_ascii=False),
            )

    return target_path


def validate_backup_data(
    session: Session,
    user_id: int,
    backup_dict: dict[str, Any] | bytes,
) -> dict[str, Any]:
    """Validasi format, struktur, dan integritas data cadangan."""
    require_user(session, user_id)

    if isinstance(backup_dict, bytes):
        backup_dict, _, _ = _read_backup_dict_from_source(backup_dict)

    if not isinstance(backup_dict, dict):
        raise ValidationError("Format cadangan tidak valid (harus berupa objek JSON).")

    backup_user_id = backup_dict.get("user_id")
    if backup_user_id is not None and backup_user_id != user_id:
        raise ValidationError(
            f"Cadangan ini milik pengguna ID {backup_user_id}, bukan pengguna saat ini ({user_id})."
        )

    version = str(backup_dict.get("version", ""))
    if not version.startswith("1."):
        raise ValidationError(f"Versi cadangan tidak didukung: {version!r}.")

    data = backup_dict.get("data")
    if not isinstance(data, dict):
        raise ValidationError("Bagian 'data' tidak ditemukan dalam cadangan.")

    required_keys = (
        "payment_methods",
        "categories",
        "opening_balances",
        "budgets",
        "recurring",
        "transactions",
    )
    for k in required_keys:
        if k not in data or not isinstance(data[k], list):
            raise ValidationError(f"Data cadangan tidak lengkap: kunci '{k}' hilang atau salah.")

    for k in ("quick_aliases", "reminder_settings"):
        if k in data and not isinstance(data[k], list):
            raise ValidationError(f"Data cadangan tidak valid: kunci '{k}' harus bertipe list.")

    # Verifikasi checksum integritas data jika tersedia
    expected_checksum = backup_dict.get("checksum")
    if expected_checksum:
        actual_checksum = _calculate_checksum(data)
        if actual_checksum != expected_checksum:
            raise ValidationError(
                "Integritas data backup tidak valid (checksum mismatch). "
                "File mungkin rusak atau telah dimodifikasi."
            )

    return backup_dict


def _read_backup_dict_from_source(
    backup_source: str | Path | dict[str, Any] | bytes,
) -> tuple[dict[str, Any], Path | None, str]:
    """Baca dan parse kamus cadangan dari file .json, .zip, dict, atau bytes."""
    if isinstance(backup_source, dict):
        return backup_source, None, "memory.json"

    if isinstance(backup_source, bytes):
        if backup_source.startswith(b"PK\x03\x04"):
            try:
                with zipfile.ZipFile(io.BytesIO(backup_source)) as zf:
                    if zf.testzip():
                        raise ValidationError("File arsip ZIP rusak atau tidak valid.")
                    namelist = zf.namelist()
                    target_name = (
                        "backup.json"
                        if "backup.json" in namelist
                        else next((n for n in namelist if n.endswith(".json")), None)
                    )
                    if not target_name:
                        raise ValidationError(
                            "File arsip ZIP tidak memiliki file backup.json yang valid."
                        )
                    raw_dict = json.loads(zf.read(target_name).decode("utf-8"))
                    return raw_dict, None, "archive.zip"
            except zipfile.BadZipFile as e:
                raise ValidationError(f"File arsip ZIP rusak atau tidak valid: {e}") from e
            except ValidationError:
                raise
            except Exception as e:
                raise ValidationError(f"File cadangan rusak atau tidak valid: {e}") from e
        else:
            try:
                raw_dict = json.loads(backup_source.decode("utf-8"))
                return raw_dict, None, "backup.json"
            except Exception as e:
                raise ValidationError(f"File cadangan rusak atau bukan JSON yang valid: {e}") from e

    p = Path(backup_source)
    if not p.is_file():
        raise NotFoundError(f"File cadangan tidak ditemukan: {p}")

    if p.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(p, "r") as zf:
                if zf.testzip():
                    raise ValidationError("File arsip ZIP rusak atau tidak valid.")
                namelist = zf.namelist()
                target_name = (
                    "backup.json"
                    if "backup.json" in namelist
                    else next((n for n in namelist if n.endswith(".json")), None)
                )
                if not target_name:
                    raise ValidationError(
                        "File arsip ZIP tidak memiliki file backup.json yang valid."
                    )
                raw_dict = json.loads(zf.read(target_name).decode("utf-8"))
                return raw_dict, p, p.name
        except zipfile.BadZipFile as e:
            raise ValidationError(f"File arsip ZIP rusak atau tidak valid: {e}") from e
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"File cadangan rusak atau tidak valid: {e}") from e
    else:
        try:
            with p.open("r", encoding="utf-8") as f:
                raw_dict = json.load(f)
                return raw_dict, p, p.name
        except Exception as e:
            raise ValidationError(f"Gagal membaca file cadangan: {e}") from e


def restore_backup(
    session: Session,
    user_id: int,
    backup_source: str | Path | dict[str, Any] | bytes,
) -> dict[str, int]:
    """Pulihkan data keuangan milik `user_id` dari cadangan.

    Operasi ini menggantikan data pengguna yang ada secara atomik tanpa memengaruhi
    pengguna lain.
    """
    require_user(session, user_id)
    raw_dict, _path, _filename = _read_backup_dict_from_source(backup_source)
    validated = validate_backup_data(session, user_id, raw_dict)
    data = validated["data"]

    # 1. Bersihkan data pengguna yang sudah ada
    queries.purge_user_data(session, user_id)

    # 2. Pulihkan Metode Pembayaran
    method_map: dict[int, int] = {}  # old_id -> new_id
    raw_current = queries.get_all_user_data(session, user_id)
    existing_methods = {m.name: m for m in raw_current["payment_methods"]}

    for m_info in data["payment_methods"]:
        m_name = m_info["name"]
        if m_name in existing_methods:
            m = existing_methods[m_name]
            m.is_archived = m_info.get("is_archived", False)
            method_map[m_info["id"]] = m.id
        else:
            m = PaymentMethod(
                user_id=user_id,
                name=m_name,
                is_archived=m_info.get("is_archived", False),
            )
            session.add(m)
            session.flush()
            method_map[m_info["id"]] = m.id

    # 3. Pulihkan Kategori
    category_map: dict[int, int] = {}  # old_id -> new_id
    raw_current = queries.get_all_user_data(session, user_id)
    existing_cats = {(c.type, c.name.lower()): c for c in raw_current["categories"]}

    for c_info in data["categories"]:
        key = (c_info["type"], c_info["name"].lower())
        if key in existing_cats:
            c = existing_cats[key]
            c.is_archived = c_info.get("is_archived", False)
            category_map[c_info["id"]] = c.id
        else:
            c = Category(
                user_id=user_id,
                name=c_info["name"],
                type=c_info["type"],
                is_archived=c_info.get("is_archived", False),
                is_system=c_info.get("is_system", False),
            )
            session.add(c)
            session.flush()
            category_map[c_info["id"]] = c.id

    # 4. Pulihkan Saldo Awal
    for ob in data["opening_balances"]:
        new_mid = method_map.get(ob["method_id"])
        if new_mid is not None:
            session.add(
                OpeningBalance(
                    user_id=user_id,
                    method_id=new_mid,
                    amount=ob["amount"],
                    effective_date=date.fromisoformat(ob["effective_date"]),
                )
            )
    session.flush()

    # 5. Pulihkan Anggaran (Budget)
    for b in data["budgets"]:
        old_cid = b.get("category_id")
        new_cid = category_map.get(old_cid) if old_cid is not None else None
        session.add(
            Budget(
                user_id=user_id,
                category_id=new_cid,
                month=b["month"],
                amount=b["amount"],
            )
        )
    session.flush()

    # 6. Pulihkan Transaksi Rutin
    for r in data["recurring"]:
        new_cid = category_map.get(r["category_id"])
        new_mid = method_map.get(r["method_id"])
        if new_cid is not None and new_mid is not None:
            session.add(
                RecurringTransaction(
                    user_id=user_id,
                    name=r["name"],
                    type=r["type"],
                    amount=r["amount"],
                    category_id=new_cid,
                    method_id=new_mid,
                    frequency=r.get("frequency", "monthly"),
                    day_of_month=r.get("day_of_month"),
                    start_date=date.fromisoformat(r["start_date"]),
                    end_date=date.fromisoformat(r["end_date"]) if r.get("end_date") else None,
                    last_generated_date=(
                        date.fromisoformat(r["last_generated_date"])
                        if r.get("last_generated_date")
                        else None
                    ),
                    note=r.get("note"),
                    is_active=r.get("is_active", True),
                )
            )
    session.flush()

    # 7. Pulihkan Transaksi (Dua tahap untuk related_transaction_id)
    tx_map: dict[int, Transaction] = {}
    for t in data["transactions"]:
        new_cid = category_map.get(t["category_id"]) if t.get("category_id") else None
        new_mid = method_map.get(t["method_id"])
        new_to_mid = method_map.get(t["to_method_id"]) if t.get("to_method_id") else None
        if new_mid is None:
            continue

        tx_obj = Transaction(
            user_id=user_id,
            type=t["type"],
            amount=t["amount"],
            date=date.fromisoformat(t["date"]),
            category_id=new_cid,
            method_id=new_mid,
            to_method_id=new_to_mid,
            related_transaction_id=None,
            note=t.get("note"),
            status=t.get("status", "active"),
        )
        if t.get("created_at"):
            tx_obj.created_at = datetime.fromisoformat(t["created_at"])
        if t.get("updated_at"):
            tx_obj.updated_at = datetime.fromisoformat(t["updated_at"])

        session.add(tx_obj)
        session.flush()
        tx_map[t["id"]] = tx_obj

    # Tahap kedua: sambungkan related_transaction_id
    for t in data["transactions"]:
        old_rel = t.get("related_transaction_id")
        if old_rel is not None and old_rel in tx_map:
            tx_map[t["id"]].related_transaction_id = tx_map[old_rel].id
    session.flush()

    # 8. Pulihkan Quick Aliases (V2)
    qa_count = 0
    for qa in data.get("quick_aliases", []):
        old_cid = qa.get("category_id")
        old_mid = qa.get("method_id")
        new_cid = category_map.get(old_cid) if old_cid is not None else None
        new_mid = method_map.get(old_mid) if old_mid is not None else None
        target_type = qa.get("target_type")
        if target_type == "category" and new_cid is None:
            continue
        if target_type == "payment_method" and new_mid is None:
            continue
        session.add(
            QuickAlias(
                user_id=user_id,
                alias=qa["alias"],
                target_type=target_type,
                category_id=new_cid,
                method_id=new_mid,
            )
        )
        qa_count += 1
    session.flush()

    # 9. Pulihkan Reminder Settings (V2)
    rs_count = 0
    for rs in data.get("reminder_settings", [])[:1]:
        session.add(
            ReminderSetting(
                user_id=user_id,
                is_daily_active=rs.get("is_daily_active", True),
                daily_time=rs.get("daily_time", "20:00"),
                is_weekly_active=rs.get("is_weekly_active", True),
                is_monthly_active=rs.get("is_monthly_active", True),
                last_daily_sent=(
                    date.fromisoformat(rs["last_daily_sent"]) if rs.get("last_daily_sent") else None
                ),
                last_weekly_sent=(
                    date.fromisoformat(rs["last_weekly_sent"])
                    if rs.get("last_weekly_sent")
                    else None
                ),
                last_monthly_sent=(
                    date.fromisoformat(rs["last_monthly_sent"])
                    if rs.get("last_monthly_sent")
                    else None
                ),
            )
        )
        rs_count += 1
    session.flush()

    if rs_count == 0:
        session.add(ReminderSetting(user_id=user_id))
        session.flush()
        rs_count = 1

    return {
        "payment_methods": len(data.get("payment_methods", [])),
        "categories": len(data.get("categories", [])),
        "opening_balances": len(data.get("opening_balances", [])),
        "budgets": len(data.get("budgets", [])),
        "recurring": len(data.get("recurring", [])),
        "transactions": len(tx_map),
        "quick_aliases": qa_count,
        "reminder_settings": rs_count,
    }


def list_backups(
    session: Session,
    user_id: int,
    directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Daftar file cadangan (.json dan .zip) yang tersedia di folder cadangan."""
    require_user(session, user_id)
    target_dir = Path(directory) if directory else config.BACKUPS_DIR
    if not target_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    candidates = sorted(
        list(target_dir.glob("*.json")) + list(target_dir.glob("*.zip")),
        key=lambda f: f.name,
    )
    for f in candidates:
        if not f.is_file():
            continue
        try:
            info, _p, fname = _read_backup_dict_from_source(f)
            if not isinstance(info, dict) or not info.get("version"):
                continue

            if info.get("user_id") is not None and info.get("user_id") != user_id:
                continue

            stat = f.stat()
            results.append(
                {
                    "path": f,
                    "filename": fname,
                    "size_bytes": stat.st_size,
                    "created_at": info.get("created_at"),
                    "user_id": info.get("user_id"),
                    "user_name": info.get("user_name"),
                    "transactions_count": info.get("summary", {}).get("transactions", 0),
                    "summary": info.get("summary", {}),
                }
            )
        except Exception:
            continue

    # Urutkan berdasarkan waktu pembuatan terbaru
    results.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return results


def get_backup_summary(
    session: Session,
    user_id: int,
    file_path: str | Path | bytes,
) -> dict[str, Any]:
    """Baca ringkasan informasi file cadangan (.json atau .zip) tanpa memulihkannya."""
    require_user(session, user_id)
    raw, p, filename = _read_backup_dict_from_source(file_path)
    validate_backup_data(session, user_id, raw)
    return {
        "path": p or Path(filename),
        "filename": filename,
        "version": raw.get("version"),
        "created_at": raw.get("created_at"),
        "user_name": raw.get("user_name"),
        "user_id": raw.get("user_id"),
        "summary": raw.get("summary", {}),
    }
