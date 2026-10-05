"""
Emite las facturas de los pedidos pagados que aún no tienen (histórico anterior
a la facturación correlativa) y las rectificativas de sus reembolsos.

Los pedidos se procesan por orden de fecha de pago, así la numeración
correlativa sigue el orden cronológico. Cada pedido va en su propia transacción.
Aplica el IVA de la configuración (TAX_RATE) a los pedidos sin tax_rate guardado.

Usage:
    cd backend
    python scripts/backfill_invoices.py --dry-run
    python scripts/backfill_invoices.py            # emite y sube los PDF
    python scripts/backfill_invoices.py --no-pdf   # emite sin subir PDF (se generan al pedirlos)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import joinedload  # noqa: E402

import app.models  # noqa: E402,F401  (registra todos los modelos)
from app.models.database import SessionLocal  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.payment import Refund  # noqa: E402
from app.services import invoicing  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra lo que haría")
    parser.add_argument("--no-pdf", action="store_true", help="No generar ni subir los PDF")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        invoiced = {row[0] for row in db.query(Invoice.order_id).filter(Invoice.invoice_type != Invoice.TYPE_CORRECTIVE)}
        orders = (
            db.query(Order)
            .options(joinedload(Order.items))
            .filter(Order.status.in_(invoicing.INVOICEABLE_STATUSES), Order.paid_at.isnot(None))
            .order_by(Order.paid_at, Order.id)
            .all()
        )
        pending = [o for o in orders if o.id not in invoiced]
        print(f"Pedidos pagados: {len(orders)} · sin factura: {len(pending)}")

        issued = correctives = 0
        for order in orders:
            refunds = (
                db.query(Refund)
                .filter(Refund.order_id == order.id, Refund.status == "succeeded")
                .order_by(Refund.created_at, Refund.id)
                .all()
            )
            needs_primary = order.id not in invoiced
            needs_corrective = [
                r for r in refunds
                if not db.query(Invoice.id).filter(Invoice.refund_id == r.id).first()
            ]
            if not needs_primary and not needs_corrective:
                continue

            if args.dry_run:
                print(f"  {order.order_number} pagado {order.paid_at:%Y-%m-%d} total {order.total} €"
                      f"{' → factura' if needs_primary else ''}"
                      f"{f' + {len(needs_corrective)} rectificativa(s)' if needs_corrective else ''}")
                continue

            try:
                created = []
                if needs_primary:
                    created.append(invoicing.issue_invoice_for_order(db, order))
                    issued += 1
                for refund in needs_corrective:
                    created.append(invoicing.issue_corrective_for_refund(db, order, refund, issued_at=refund.created_at))
                    correctives += 1
                db.commit()
                for invoice in created:
                    if not args.no_pdf:
                        invoicing.store_pdf(db, invoice)
                        db.commit()
                    print(f"  {order.order_number} → {invoice.invoice_number} ({invoice.pdf_status})")
            except Exception as exc:
                db.rollback()
                print(f"  ERROR {order.order_number}: {exc}", file=sys.stderr)

        if not args.dry_run:
            print(f"Emitidas: {issued} facturas y {correctives} rectificativas")
    finally:
        db.close()


if __name__ == "__main__":
    main()
