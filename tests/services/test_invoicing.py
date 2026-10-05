"""Tests de emisión de facturas (services/invoicing.py)."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.order import Order, OrderItem
from app.models.payment import Refund
from app.models.user import User
from app.services import invoicing, private_blob
from app.services.invoice import render_invoice_pdf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_order(db, *, number="CC-261005-AAAAAA", total="49.95", paid_at=datetime(2026, 10, 5, 10, 0),
               status="paid", billing=None, user=None, tax_rate=Decimal("0.10")):
    order = Order(
        order_number=number,
        status=status,
        subtotal=Decimal(total) - Decimal("4.95"),
        shipping_cost=Decimal("4.95"),
        discount=Decimal("0"),
        tax=Decimal("0"),
        tax_rate=tax_rate,
        total=Decimal(total),
        guest_email=None if user else "cliente@example.com",
        user_id=user.id if user else None,
        payment_method="card",
        paid_at=paid_at,
        shipping_address_json=json.dumps({
            "first_name": "Ana", "last_name": "Pérez", "street": "Calle Mayor 1",
            "city": "Madrid", "province": "Madrid", "postal_code": "28001",
            "country": "España", "phone": "600000000",
        }),
    )
    if billing:
        order.billing_address = billing
    db.add(order)
    db.flush()
    db.add(OrderItem(
        order_id=order.id, product_name="Crema Pistacho Pura 200g", product_sku="PURA-200",
        quantity=1, unit_price=order.subtotal, total=order.subtotal,
    ))
    db.flush()
    db.refresh(order)
    return order


def make_refund(db, order, *, amount_cents, status="succeeded", rid="re_1"):
    refund = Refund(order_id=order.id, stripe_refund_id=rid, amount=amount_cents,
                    reason="requested_by_customer", status=status)
    db.add(refund)
    db.flush()
    return refund


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------

def test_split_tax_includes_vat_and_adds_up():
    base, tax = invoicing.split_tax(Decimal("49.95"), Decimal("0.10"))
    assert tax == Decimal("4.54")
    assert base == Decimal("45.41")
    assert base + tax == Decimal("49.95")


def test_split_tax_negative_amounts_for_correctives():
    base, tax = invoicing.split_tax(Decimal("-49.95"), Decimal("0.10"))
    assert base + tax == Decimal("-49.95")
    assert tax == Decimal("-4.54")


def test_month_bounds_use_peninsular_time():
    start, end = invoicing.month_bounds_utc(2026, 10)
    assert start == datetime(2026, 9, 30, 22, 0)   # 1 oct 00:00 CEST
    assert end == datetime(2026, 10, 31, 23, 0)    # 1 nov 00:00 CET


# ---------------------------------------------------------------------------
# Numeración y emisión
# ---------------------------------------------------------------------------

def test_invoices_are_correlative_per_series_and_year(db):
    first = invoicing.issue_invoice_for_order(db, make_order(db, number="CC-1"))
    second = invoicing.issue_invoice_for_order(db, make_order(db, number="CC-2"))
    assert first.invoice_number == "F2026-000001"
    assert second.invoice_number == "F2026-000002"
    assert (second.series, second.year, second.number) == ("F", 2026, 2)


def test_issue_is_idempotent(db):
    order = make_order(db)
    first = invoicing.issue_invoice_for_order(db, order)
    again = invoicing.issue_invoice_for_order(db, order)
    assert again.id == first.id
    assert db.query(Invoice).count() == 1


def test_year_follows_madrid_time(db):
    # 31/12/2026 23:30 UTC = 1/1/2027 00:30 en Madrid → serie 2027
    order = make_order(db, paid_at=datetime(2026, 12, 31, 23, 30))
    invoice = invoicing.issue_invoice_for_order(db, order)
    assert invoice.invoice_number == "F2027-000001"
    assert invoicing.pdf_pathname(invoice) == "invoices/2027/01/F2027-000001.pdf"


def test_unpaid_order_cannot_be_invoiced(db):
    order = make_order(db, status="pending_payment")
    with pytest.raises(ValueError):
        invoicing.issue_invoice_for_order(db, order)


def test_snapshot_amounts_and_vat(db):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db, total="49.95"))
    assert invoice.invoice_type == Invoice.TYPE_SIMPLIFIED
    assert invoice.total == Decimal("49.95")
    assert invoice.tax_rate == Decimal("0.10")
    assert invoice.tax_base + invoice.tax_amount == invoice.total
    assert invoice.buyer_snapshot["name"] == "Ana Pérez"
    assert invoice.buyer_snapshot["email"] == "cliente@example.com"
    assert invoice.seller_snapshot["nif"] == "B56673700"
    assert invoice.lines_snapshot["items"][0]["total"] == "45.00"


def test_missing_order_tax_rate_uses_configured_rate(db):
    order = make_order(db, tax_rate=None)
    invoice = invoicing.issue_invoice_for_order(db, order)
    assert invoice.tax_rate == Decimal("0.10")
    assert order.tax_rate == Decimal("0.10")


def test_billing_with_nif_produces_full_invoice(db):
    order = make_order(db, billing={
        "name": "Gourmet Ciudad Real SL", "nif": "b56673700", "street": "Calle Toledo 5",
        "city": "Ciudad Real", "province": "Ciudad Real", "postal_code": "13001", "country": "España",
    })
    invoice = invoicing.issue_invoice_for_order(db, order)
    assert invoice.invoice_type == Invoice.TYPE_FULL
    assert invoice.buyer_snapshot["nif"] == "B56673700"
    assert invoice.buyer_snapshot["name"] == "Gourmet Ciudad Real SL"
    assert invoice.buyer_snapshot["address"]["street"] == "Calle Toledo 5"


def test_snapshot_survives_customer_changes(db):
    user = User(email="ana@example.com", first_name="Ana", last_name="Pérez", password_hash="x")
    db.add(user)
    db.flush()
    order = make_order(db, user=user)
    invoice = invoicing.issue_invoice_for_order(db, order)
    user.email = "deleted_x@cremacuadrado.invalid"
    db.flush()
    assert invoice.buyer_snapshot["email"] == "ana@example.com"


def test_hash_chain_links_previous_invoice(db):
    first = invoicing.issue_invoice_for_order(db, make_order(db, number="CC-1"))
    second = invoicing.issue_invoice_for_order(db, make_order(db, number="CC-2"))
    assert first.previous_hash is None
    assert second.previous_hash == first.record_hash
    assert second.record_hash == invoicing.compute_record_hash(second)
    assert len(second.record_hash) == 64


# ---------------------------------------------------------------------------
# Rectificativas
# ---------------------------------------------------------------------------

def test_full_refund_creates_corrective_in_r_series(db):
    order = make_order(db, total="49.95")
    original = invoicing.issue_invoice_for_order(db, order)
    refund = make_refund(db, order, amount_cents=4995)

    corrective = invoicing.issue_corrective_for_refund(db, order, refund)
    assert corrective.invoice_number == "R2026-000001"
    assert corrective.invoice_type == Invoice.TYPE_CORRECTIVE
    assert corrective.rectifies_invoice_id == original.id
    assert corrective.total == Decimal("-49.95")
    assert corrective.tax_base + corrective.tax_amount == Decimal("-49.95")
    assert corrective.lines_snapshot["rectified_invoice_number"] == original.invoice_number
    assert corrective.buyer_snapshot == original.buyer_snapshot

    # Idempotente por reembolso
    assert invoicing.issue_corrective_for_refund(db, order, refund).id == corrective.id


def test_partial_refund_creates_single_line_corrective(db):
    order = make_order(db, total="49.95")
    invoicing.issue_invoice_for_order(db, order)
    corrective = invoicing.issue_corrective_for_refund(db, order, make_refund(db, order, amount_cents=1000))
    assert corrective.total == Decimal("-10.00")
    assert len(corrective.lines_snapshot["items"]) == 1
    assert "parcial" in corrective.lines_snapshot["items"][0]["description"]


def test_pending_refund_is_not_invoiced(db):
    order = make_order(db)
    refund = make_refund(db, order, amount_cents=1000, status="pending")
    assert invoicing.issue_corrective_for_refund(db, order, refund) is None


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def test_pdf_render_is_deterministic(db):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    first = render_invoice_pdf(invoice)
    assert first.startswith(b"%PDF")
    assert hashlib.sha256(first).digest() == hashlib.sha256(render_invoice_pdf(invoice)).digest()


def test_corrective_pdf_renders(db):
    order = make_order(db)
    invoicing.issue_invoice_for_order(db, order)
    corrective = invoicing.issue_corrective_for_refund(db, order, make_refund(db, order, amount_cents=4995))
    assert render_invoice_pdf(corrective).startswith(b"%PDF")


def test_pdf_without_blob_configured_stays_pending(db):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    pdf = invoicing.get_pdf(db, invoice)
    assert pdf.startswith(b"%PDF")
    assert invoice.pdf_status == "pending"


class FakeBlob:
    def __init__(self):
        self.files = {}
        self.uploads = 0

    def upload(self, content, pathname, content_type="application/pdf"):
        self.uploads += 1
        self.files[pathname] = content
        return pathname

    def download(self, pathname):
        if pathname not in self.files:
            raise private_blob.BlobNotFound(pathname)
        return self.files[pathname]


@pytest.fixture()
def fake_blob(monkeypatch):
    blob = FakeBlob()
    monkeypatch.setattr(private_blob, "is_configured", lambda: True)
    monkeypatch.setattr(private_blob, "upload", blob.upload)
    monkeypatch.setattr(private_blob, "download", blob.download)
    return blob


def test_stored_pdf_is_reused_on_resend(db, fake_blob):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    stored = invoicing.store_pdf(db, invoice)
    assert invoice.pdf_status == "stored"
    assert invoice.blob_pathname == "invoices/2026/10/F2026-000001.pdf"
    assert invoice.pdf_sha256 == hashlib.sha256(stored).hexdigest()

    resent = invoicing.get_pdf(db, invoice)
    assert resent == stored
    assert fake_blob.uploads == 1  # no se vuelve a subir


def test_missing_blob_is_regenerated_from_snapshot(db, fake_blob):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    original = invoicing.store_pdf(db, invoice)
    fake_blob.files.clear()

    pdf = invoicing.get_pdf(db, invoice)
    assert pdf == original  # determinista
    assert fake_blob.uploads == 2


def test_tampered_blob_is_replaced(db, fake_blob):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    original = invoicing.store_pdf(db, invoice)
    fake_blob.files[invoice.blob_pathname] = b"%PDF-tampered"
    assert invoicing.get_pdf(db, invoice) == original


def test_upload_failure_marks_failed_but_returns_pdf(db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(private_blob, "is_configured", lambda: True)
    monkeypatch.setattr(private_blob, "upload", boom)
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    assert invoicing.store_pdf(db, invoice).startswith(b"%PDF")
    assert invoice.pdf_status == "failed"


def test_mark_sent_counts(db):
    invoice = invoicing.issue_invoice_for_order(db, make_order(db))
    invoicing.mark_sent(invoice)
    invoicing.mark_sent(invoice)
    assert invoice.sent_count == 2
    assert invoice.last_sent_at is not None
