"""
Orders API endpoints.
"""
import io
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, CurrentUser
from app.models.invoice import Invoice
from app.models.order import Order, OrderItem
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.schemas.order import OrderResponse, OrderListResponse, OrderItemResponse
from app.schemas.common import Message, PaginatedResponse
from app.schemas.invoice import InvoiceSummary
from app.services import invoicing
from app.services.email import send_invoice_email
from app.utils.url import normalize_image_url
from app.config import settings

logger = logging.getLogger("cremacuadrado.orders")

router = APIRouter()


@router.get("", response_model=PaginatedResponse[OrderListResponse])
def list_orders(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
):
    """Get current user's orders."""
    query = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.product).joinedload(Product.images)
    ).filter(Order.user_id == current_user.id).order_by(Order.created_at.desc())

    total = query.count()
    orders = query.offset((page - 1) * page_size).limit(page_size).all()

    def _primary_image(order: Order) -> str | None:
        if not order.items:
            return None
        item = order.items[0]
        if item.product:
            return normalize_image_url(item.product.primary_image)
        return normalize_image_url(item.product_image_url)

    return PaginatedResponse.create(
        [
            OrderListResponse(
                id=order.id,
                order_number=order.order_number,
                status=order.status,
                total=order.total,
                item_count=order.item_count,
                created_at=order.created_at,
                primary_image_url=_primary_image(order),
            )
            for order in orders
        ],
        total, page, page_size
    )


@router.get("/{order_number}", response_model=OrderResponse)
def get_order(order_number: str, db: DbSession, current_user: CurrentUser):
    """Get order details by order number."""
    order = db.query(Order).options(
        joinedload(Order.items).joinedload(OrderItem.product).joinedload(Product.images)
    ).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    items = [
        OrderItemResponse(
            id=item.id,
            product_id=item.product_id,
            product_name=item.product_name,
            product_sku=item.product_sku,
            product_image_url=(
                normalize_image_url(item.product.primary_image)
                if item.product
                else normalize_image_url(item.product_image_url)
            ),
            quantity=item.quantity,
            unit_price=item.unit_price,
            total=item.total,
        )
        for item in order.items
    ]

    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        status=order.status,
        subtotal=order.subtotal,
        shipping_cost=order.shipping_cost,
        discount=order.discount,
        coupon_code=order.coupon_code,
        tax=order.tax,
        total=order.total,
        shipping_address=order.shipping_address,
        billing_address=order.billing_address,
        payment_method=order.payment_method,
        tracking_number=order.tracking_number,
        customer_notes=order.customer_notes,
        items=items,
        item_count=order.item_count,
        created_at=order.created_at,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
    )


@router.post("/{order_number}/reorder", response_model=Message)
def reorder(order_number: str, db: DbSession, current_user: CurrentUser):
    """Add items from a previous order to cart."""
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    from app.models.product import ProductVariant

    # Batch-load variants and products for all order items
    variant_ids = [item.product_variant_id for item in order.items if item.product_variant_id]
    variants_by_id = {
        v.id: v for v in db.query(ProductVariant).filter(
            ProductVariant.id.in_(variant_ids),
            ProductVariant.is_active == True,
        ).all()
    } if variant_ids else {}

    product_ids = [item.product_id for item in order.items if item.product_id]
    products_by_id = {
        p.id: p for p in db.query(Product).filter(
            Product.id.in_(product_ids),
            Product.is_active == True,
        ).all()
    }

    cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    if not cart:
        cart = Cart(user_id=current_user.id)
        db.add(cart)
        db.flush()

    cart_items_by_variant = {
        ci.product_variant_id: ci for ci in db.query(CartItem).filter(
            CartItem.cart_id == cart.id,
            CartItem.product_variant_id.in_(variant_ids),
        ).all()
    } if variant_ids else {}

    added_count = 0
    unavailable = []

    for order_item in order.items:
        if not order_item.product_id or not order_item.product_variant_id:
            unavailable.append(order_item.product_name)
            continue

        product = products_by_id.get(order_item.product_id)
        if not product:
            unavailable.append(order_item.product_name)
            continue

        variant = variants_by_id.get(order_item.product_variant_id)
        if not variant or not variant.is_in_stock:
            unavailable.append(order_item.product_name)
            continue

        quantity_to_add = min(order_item.quantity, variant.stock)
        existing_item = cart_items_by_variant.get(variant.id)

        if existing_item:
            existing_item.quantity = min(existing_item.quantity + quantity_to_add, variant.stock)
        else:
            db.add(CartItem(
                cart_id=cart.id,
                product_id=product.id,
                product_variant_id=variant.id,
                quantity=quantity_to_add,
                price_at_add=variant.price,
            ))

        added_count += 1

    db.commit()

    if unavailable:
        message = f"Se añadieron {added_count} productos. No disponibles: {', '.join(unavailable)}"
    else:
        message = f"Se añadieron {added_count} productos al carrito"

    return Message(message=message)


@router.post("/{order_number}/cancel", response_model=Message)
def cancel_order(order_number: str, db: DbSession, current_user: CurrentUser):
    """Cancel a pending order."""
    order = db.query(Order).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id,
    ).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    if order.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden cancelar pedidos en estado pendiente",
        )

    order.status = "cancelled"
    db.commit()
    return Message(message="Pedido cancelado correctamente")


def _get_owned_order(db, order_number: str, user_id: int) -> Order:
    order = db.query(Order).options(joinedload(Order.items)).filter(
        Order.order_number == order_number,
        Order.user_id == user_id,
    ).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")
    return order


def _ensure_primary_invoice(db, order: Order) -> Invoice:
    """Factura del pedido; la emite si no existe (pedidos anteriores a la facturación)."""
    invoice = invoicing.primary_invoice(db, order.id)
    if invoice:
        return invoice
    if order.status not in invoicing.INVOICEABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La factura solo está disponible para pedidos pagados",
        )
    invoice = invoicing.issue_invoice_for_order(db, order)
    db.commit()
    return invoice


def _pdf_response(pdf_bytes: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{order_number}/invoices", response_model=List[InvoiceSummary])
def list_order_invoices(order_number: str, db: DbSession, current_user: CurrentUser):
    """Facturas (ordinaria y rectificativas) de un pedido del usuario."""
    order = _get_owned_order(db, order_number, current_user.id)
    invoices = (
        db.query(Invoice)
        .filter(Invoice.order_id == order.id)
        .order_by(Invoice.issued_at, Invoice.id)
        .all()
    )
    return [InvoiceSummary.model_validate(inv) for inv in invoices]


@router.get("/{order_number}/invoice")
def download_invoice(
    order_number: str,
    db: DbSession,
    current_user: CurrentUser,
    number: str | None = Query(None, description="Número de factura; por defecto la del pedido"),
):
    """Descarga el PDF guardado de la factura (o de una rectificativa con ?number=)."""
    order = _get_owned_order(db, order_number, current_user.id)
    if number:
        invoice = db.query(Invoice).filter(
            Invoice.order_id == order.id, Invoice.invoice_number == number,
        ).first()
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factura no encontrada")
    else:
        invoice = _ensure_primary_invoice(db, order)

    pdf_bytes = invoicing.get_pdf(db, invoice)
    db.commit()
    return _pdf_response(pdf_bytes, invoice.pdf_filename)


@router.post("/{order_number}/request-invoice", response_model=Message)
def request_invoice(order_number: str, db: DbSession, current_user: CurrentUser):
    """Reenvía por email la factura YA EMITIDA del pedido (el mismo PDF guardado)."""
    order = _get_owned_order(db, order_number, current_user.id)
    invoice = _ensure_primary_invoice(db, order)

    pdf_bytes = invoicing.get_pdf(db, invoice)
    customer_email = current_user.email
    sent = send_invoice_email(
        to_email=customer_email,
        first_name=current_user.first_name or "",
        order_number=order.order_number,
        invoice_number=invoice.invoice_number,
        pdf_bytes=pdf_bytes,
    )
    if not sent:
        db.commit()  # keep pdf_status/blob updates even if the email failed
        logger.error("Invoice email failed: invoice=%s order=%s", invoice.invoice_number, order_number)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No hemos podido enviar la factura. Inténtalo de nuevo más tarde o descárgala.",
        )

    invoicing.mark_sent(invoice)
    db.commit()
    logger.info("Invoice email sent: invoice=%s order=%s", invoice.invoice_number, order_number)
    return Message(message=f"Factura {invoice.invoice_number} enviada a {customer_email}")
