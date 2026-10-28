"""Tests de validación de NIF/CIF/NIE y del cliente del Blob privado."""
import pytest

from app.config import settings
from app.services import private_blob
from app.utils.tax_id import is_valid_tax_id, normalize_tax_id


@pytest.mark.parametrize("value", [
    "12345678Z",      # NIF
    "12345678-z",     # con guion y minúscula
    "X1234567L",      # NIE
    "B56673700",      # CIF de CREMACUADRADO SL
    "Q2826000H",      # CIF organismo (control letra)
])
def test_valid_tax_ids(value):
    assert is_valid_tax_id(value)


@pytest.mark.parametrize("value", ["12345678A", "X1234567A", "B56673701", "ABC", ""])
def test_invalid_tax_ids(value):
    assert not is_valid_tax_id(value)


def test_normalize_tax_id():
    assert normalize_tax_id(" b-56.673.700 ") == "B56673700"


def test_store_id_from_explicit_setting(monkeypatch):
    monkeypatch.setattr(settings, "BLOB_INVOICE_READ_WRITE_TOKEN", "vercel_blob_rw_abc123_secret")
    monkeypatch.setattr(settings, "BLOB_INVOICE_STORE_ID", "store_XyZ")
    assert private_blob.store_id() == "XyZ"


def test_store_id_derived_from_token(monkeypatch):
    monkeypatch.setattr(settings, "BLOB_INVOICE_READ_WRITE_TOKEN", "vercel_blob_rw_abc123_secret")
    monkeypatch.setattr(settings, "BLOB_INVOICE_STORE_ID", "")
    assert private_blob.store_id() == "abc123"


def test_upload_uses_private_access_headers(monkeypatch):
    monkeypatch.setattr(settings, "BLOB_INVOICE_READ_WRITE_TOKEN", "vercel_blob_rw_abc123_secret")
    monkeypatch.setattr(settings, "BLOB_INVOICE_STORE_ID", "")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"pathname": "invoices/2026/10/F2026-000001.pdf"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put(self, url, content, headers):
            captured.update(url=url, content=content, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(private_blob.httpx, "Client", FakeClient)
    private_blob.upload(b"%PDF", "invoices/2026/10/F2026-000001.pdf")

    assert captured["url"].startswith("https://vercel.com/api/blob/?pathname=invoices%2F2026")
    assert captured["headers"]["x-vercel-blob-access"] == "private"
    assert captured["headers"]["x-vercel-blob-store-id"] == "abc123"
    assert captured["headers"]["authorization"] == "Bearer vercel_blob_rw_abc123_secret"
    assert captured["headers"]["x-add-random-suffix"] == "0"


def test_missing_token_raises(monkeypatch):
    monkeypatch.setattr(settings, "BLOB_INVOICE_READ_WRITE_TOKEN", "")
    assert not private_blob.is_configured()
    with pytest.raises(private_blob.BlobNotConfigured):
        private_blob.upload(b"x", "a.pdf")
