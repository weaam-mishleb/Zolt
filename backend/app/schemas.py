"""Pydantic response models for the public API."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    barcode: str
    name: str
    manufacturer: str | None = None
    unit_qty: str | None = None
    unit_of_measure: str | None = None


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chain_id: str
    chain_name: str
    sub_chain_id: str | None = None
    store_code: str
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    zip_code: str | None = None
