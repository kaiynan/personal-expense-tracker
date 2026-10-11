"""Base class model ORM dan konvensi penamaan constraint."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Nama constraint yang konsisten wajib agar Alembic bisa mengubah/menghapusnya
# dengan andal (terutama di SQLite).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base semua model; menyimpan metadata skema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
