"""Service audit dan riwayat perubahan: pencatatan aktivitas dan pembalikan aksi (undo).

Semua fungsi sinkron, menerima `Session` dan `user_id`, dan tidak melakukan commit
agar pemanggil dapat mengaturnya di dalam `session_scope`.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from database import queries
from models import (
    ACTION_CREATE,
    ACTION_DELETE,
    ACTION_RESTORE,
    ACTION_UNDO,
    ACTION_UPDATE,
    AUDIT_ACTIONS,
    STATUS_ACTIVE,
    STATUS_DELETED,
    AuditLog,
    Transaction,
)
from services.common import (
    balance_snapshot,
    before_opening_warnings,
    negative_balance_warnings,
    newer_adjustment_warnings,
    require_user,
)
from services.results import UndoResult
from utils.errors import NotFoundError, ValidationError


def _snapshot_transaction(
    tx: Transaction, related: list[Transaction] | None = None
) -> dict[str, Any]:
    """Buat representasi kamus dari status transaksi untuk serialisasi audit log."""
    data: dict[str, Any] = {
        "id": tx.id,
        "type": tx.type,
        "amount": tx.amount,
        "date": tx.date.isoformat(),
        "category_id": tx.category_id,
        "method_id": tx.method_id,
        "to_method_id": tx.to_method_id,
        "related_transaction_id": tx.related_transaction_id,
        "note": tx.note,
        "status": tx.status,
    }
    if related:
        data["related"] = [_snapshot_transaction(r) for r in related]
    return data


def record_audit_log(
    session: Session,
    user_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    *,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    description: str | None = None,
) -> AuditLog:
    """Catat satu entri ke dalam log audit untuk pengguna."""
    require_user(session, user_id)
    if action not in AUDIT_ACTIONS:
        raise ValidationError(f"Aksi audit '{action}' tidak valid.")

    old_json = json.dumps(old_values, ensure_ascii=False) if old_values is not None else None
    new_json = json.dumps(new_values, ensure_ascii=False) if new_values is not None else None

    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_values=old_json,
        new_values=new_json,
        description=description,
        is_undone=False,
    )
    session.add(entry)
    session.flush()
    return entry


def get_audit_log(session: Session, user_id: int, audit_log_id: int) -> AuditLog:
    """Ambil satu entri audit log milik `user_id`. Melempar NotFoundError bila tidak ada."""
    require_user(session, user_id)
    entry = queries.get_audit_log(session, user_id, audit_log_id)
    if entry is None:
        raise NotFoundError(f"Entri riwayat #{audit_log_id} tidak ditemukan.")
    return entry


def list_audit_logs(
    session: Session,
    user_id: int,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    action: str | None = None,
    limit: int | None = 50,
) -> list[AuditLog]:
    """Daftar riwayat perubahan milik `user_id` (terbaru lebih dulu)."""
    require_user(session, user_id)
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        raise ValidationError("Batas jumlah harus bilangan bulat positif.")
    if action is not None and action not in AUDIT_ACTIONS:
        raise ValidationError(f"Filter aksi audit '{action}' tidak valid.")

    return queries.list_audit_logs(
        session,
        user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        limit=limit,
    )


def undo_action(session: Session, user_id: int, audit_log_id: int) -> UndoResult:
    """Batalkan perubahan dari suatu aksi audit log tertentu."""
    target_log = get_audit_log(session, user_id, audit_log_id)
    if target_log.is_undone:
        raise ValidationError(f"Aksi #{target_log.id} sudah pernah dibatalkan (undo).")
    if target_log.action == ACTION_UNDO:
        raise ValidationError(f"Aksi undo #{target_log.id} tidak dapat dibatalkan kembali.")
    if target_log.entity_type != "transaction":
        raise ValidationError(
            f"Entitas '{target_log.entity_type}' belum mendukung pembatalan (undo)."
        )

    before = balance_snapshot(session, user_id)
    action = target_log.action

    if action == ACTION_CREATE:
        tx = queries.get_transaction(session, user_id, target_log.entity_id, include_deleted=True)
        if tx is None:
            raise NotFoundError(f"Transaksi #{target_log.entity_id} tidak ditemukan.")
        if tx.status == STATUS_DELETED:
            raise ValidationError(f"Transaksi #{tx.id} sudah dalam keadaan terhapus.")

        related = queries.get_related_transactions(session, user_id, tx.id, include_deleted=False)
        affected = [tx.method_id, tx.to_method_id] + [
            m for rel in related for m in (rel.method_id, rel.to_method_id)
        ]

        tx.status = STATUS_DELETED
        for rel in related:
            rel.status = STATUS_DELETED

        warnings = newer_adjustment_warnings(session, user_id, tx.date, affected, exclude_id=tx.id)
        warnings += negative_balance_warnings(session, user_id, before, affected)

        undo_desc = f"Membatalkan penambahan Transaksi #{tx.id}"
        undo_log = AuditLog(
            user_id=user_id,
            action=ACTION_UNDO,
            entity_type="transaction",
            entity_id=tx.id,
            old_values=target_log.new_values,
            new_values=json.dumps({"status": STATUS_DELETED}),
            description=undo_desc,
            is_undone=True,
        )
        session.add(undo_log)
        target_log.is_undone = True
        session.flush()
        return UndoResult(undo_log, tx, action, warnings)

    elif action == ACTION_DELETE:
        tx = queries.get_transaction(session, user_id, target_log.entity_id, include_deleted=True)
        if tx is None:
            raise NotFoundError(f"Transaksi #{target_log.entity_id} tidak ditemukan.")
        if tx.status == STATUS_ACTIVE:
            raise ValidationError(f"Transaksi #{tx.id} sudah dalam keadaan aktif.")

        related = [
            rel
            for rel in queries.get_related_transactions(
                session, user_id, tx.id, include_deleted=True
            )
            if rel.status == STATUS_DELETED
        ]

        tx.status = STATUS_ACTIVE
        for rel in related:
            rel.status = STATUS_ACTIVE

        affected = [tx.method_id, tx.to_method_id] + [
            m for rel in related for m in (rel.method_id, rel.to_method_id)
        ]
        warnings = before_opening_warnings(
            session, user_id, tx.date, [tx.method_id, tx.to_method_id]
        )
        for rel in related:
            if rel.date != tx.date:
                warnings += before_opening_warnings(
                    session, user_id, rel.date, [rel.method_id, rel.to_method_id]
                )
        warnings += newer_adjustment_warnings(session, user_id, tx.date, affected, exclude_id=tx.id)
        warnings += negative_balance_warnings(session, user_id, before, affected)

        undo_desc = f"Membatalkan penghapusan Transaksi #{tx.id}"
        undo_log = AuditLog(
            user_id=user_id,
            action=ACTION_UNDO,
            entity_type="transaction",
            entity_id=tx.id,
            old_values=json.dumps({"status": STATUS_DELETED}),
            new_values=json.dumps({"status": STATUS_ACTIVE}),
            description=undo_desc,
            is_undone=True,
        )
        session.add(undo_log)
        target_log.is_undone = True
        session.flush()
        return UndoResult(undo_log, tx, action, warnings)

    elif action == ACTION_RESTORE:
        tx = queries.get_transaction(session, user_id, target_log.entity_id, include_deleted=True)
        if tx is None:
            raise NotFoundError(f"Transaksi #{target_log.entity_id} tidak ditemukan.")
        if tx.status == STATUS_DELETED:
            raise ValidationError(f"Transaksi #{tx.id} sudah dalam keadaan terhapus.")

        related = queries.get_related_transactions(session, user_id, tx.id, include_deleted=False)
        affected = [tx.method_id, tx.to_method_id] + [
            m for rel in related for m in (rel.method_id, rel.to_method_id)
        ]

        tx.status = STATUS_DELETED
        for rel in related:
            rel.status = STATUS_DELETED

        warnings = newer_adjustment_warnings(session, user_id, tx.date, affected, exclude_id=tx.id)
        warnings += negative_balance_warnings(session, user_id, before, affected)

        undo_desc = f"Membatalkan pemulihan Transaksi #{tx.id}"
        undo_log = AuditLog(
            user_id=user_id,
            action=ACTION_UNDO,
            entity_type="transaction",
            entity_id=tx.id,
            old_values=json.dumps({"status": STATUS_ACTIVE}),
            new_values=json.dumps({"status": STATUS_DELETED}),
            description=undo_desc,
            is_undone=True,
        )
        session.add(undo_log)
        target_log.is_undone = True
        session.flush()
        return UndoResult(undo_log, tx, action, warnings)

    elif action == ACTION_UPDATE:
        tx = queries.get_transaction(session, user_id, target_log.entity_id, include_deleted=True)
        if tx is None:
            raise NotFoundError(f"Transaksi #{target_log.entity_id} tidak ditemukan.")
        if tx.status != STATUS_ACTIVE:
            raise ValidationError(
                f"Transaksi #{tx.id} sudah dihapus; pulihkan dulu sebelum membatalkan perubahannya."
            )
        if not target_log.old_values:
            raise ValidationError(f"Tidak ada data nilai lama pada riwayat #{target_log.id}.")

        old_data: dict[str, Any] = json.loads(target_log.old_values)
        old_date = date.fromisoformat(str(old_data["date"])) if old_data.get("date") else tx.date
        old_amount = int(old_data["amount"]) if "amount" in old_data else tx.amount
        old_cat_id = old_data.get("category_id")
        old_method_id = old_data.get("method_id", tx.method_id)
        old_to_method_id = old_data.get("to_method_id")
        old_note = old_data.get("note")

        related = queries.get_related_transactions(session, user_id, tx.id, include_deleted=False)
        affected = [tx.method_id, tx.to_method_id, old_method_id, old_to_method_id] + [
            m for rel in related for m in (rel.method_id, rel.to_method_id)
        ]

        tx.amount = old_amount
        tx.date = old_date
        tx.category_id = old_cat_id
        tx.method_id = old_method_id
        tx.to_method_id = old_to_method_id
        tx.note = old_note

        if "date" in old_data:
            for rel in related:
                rel.date = tx.date
        if "method_id" in old_data:
            for rel in related:
                rel.method_id = tx.method_id

        warnings = []
        if (
            tx.date != old_date
            or tx.method_id != old_method_id
            or tx.to_method_id != old_to_method_id
        ):
            warnings += before_opening_warnings(
                session, user_id, tx.date, [tx.method_id, tx.to_method_id]
            )
            for rel in related:
                if rel.date != tx.date:
                    warnings += before_opening_warnings(
                        session, user_id, rel.date, [rel.method_id, rel.to_method_id]
                    )
        warnings += newer_adjustment_warnings(
            session,
            user_id,
            tx.date,
            affected,
            exclude_id=tx.id,
        )
        warnings += negative_balance_warnings(session, user_id, before, affected)

        undo_desc = f"Membatalkan perubahan Transaksi #{tx.id}"
        undo_log = AuditLog(
            user_id=user_id,
            action=ACTION_UNDO,
            entity_type="transaction",
            entity_id=tx.id,
            old_values=target_log.new_values,
            new_values=target_log.old_values,
            description=undo_desc,
            is_undone=True,
        )
        session.add(undo_log)
        target_log.is_undone = True
        session.flush()
        return UndoResult(undo_log, tx, action, warnings)

    else:
        raise ValidationError(f"Aksi '{action}' tidak dapat dibatalkan.")


def undo_last(session: Session, user_id: int) -> UndoResult:
    """Batalkan aksi perubahan transaksi terakhir yang belum di-undo."""
    require_user(session, user_id)
    last_log = queries.get_last_undoable_log(session, user_id)
    if last_log is None:
        raise ValidationError("Tidak ada riwayat perubahan yang dapat dibatalkan (undo).")
    return undo_action(session, user_id, last_log.id)
