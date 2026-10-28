"""Invoice schemas."""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


class InvoiceSummary(BaseModel):
    """Factura vista por el cliente."""
    invoice_number: str
    invoice_type: str
    issued_at: datetime
    total: Decimal

    model_config = {"from_attributes": True}


class AdminInvoiceItem(BaseModel):
    """Fila del listado de facturas del panel admin."""
    id: int
    invoice_number: str
    invoice_type: str
    issued_at: datetime
    order_id: int
    order_number: Optional[str] = None
    user_id: Optional[int] = None
    buyer_name: Optional[str] = None
    buyer_nif: Optional[str] = None
    buyer_email: Optional[str] = None
    tax_base: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    rectifies_number: Optional[str] = None
    pdf_status: str
    sent_count: int
    last_sent_at: Optional[datetime] = None


class AdminInvoiceTotals(BaseModel):
    """Totales del periodo filtrado."""
    count: int
    tax_base: Decimal
    tax_amount: Decimal
    total: Decimal
