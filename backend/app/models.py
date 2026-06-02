"""SQLAlchemy ORM models mirroring db/init/01_schema.sql.

The schema is owned by the SQL init script; these models are for querying
(search service now, ETL upserts and the comparison engine later).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(20))
    chain_name: Mapped[str] = mapped_column(String(100))
    sub_chain_id: Mapped[str] = mapped_column(String(20), default="")
    store_code: Mapped[str] = mapped_column(String(20))
    store_name: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    zip_code: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["Price"]] = relationship(back_populates="store")

    __table_args__ = (
        UniqueConstraint("chain_id", "sub_chain_id", "store_code", name="uq_store_natural"),
        Index("idx_store_city", "city"),
        Index("idx_store_chain", "chain_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(String(255))
    unit_qty: Mapped[str | None] = mapped_column(String(50))
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3))
    unit_of_measure: Mapped[str | None] = mapped_column(String(50))
    is_weighted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["Price"]] = relationship(back_populates="product")

    __table_args__ = (UniqueConstraint("barcode", name="uq_product_barcode"),)


class Price(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    allow_discount: Mapped[bool] = mapped_column(Boolean, default=True)
    item_status: Mapped[str | None] = mapped_column(String(20))
    price_update_time: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="prices")
    store: Mapped["Store"] = relationship(back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "store_id", name="uq_price_product_store"),
        Index("idx_price_store", "store_id"),
    )
