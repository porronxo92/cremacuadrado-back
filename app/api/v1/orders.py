"""
Orders API endpoints.
"""
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, CurrentUser
from app.models.order import Order, OrderItem
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.schemas.order import OrderResponse, OrderListResponse
from app.schemas.common import Message, PaginatedResponse
from app.config import settings

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
        joinedload(Order.items)
    ).filter(Order.user_id == current_user.id).order_by(Order.created_at.desc())

    total = query.count()
    orders = query.offset((page - 1) * page_size).limit(page_size).all()

    def _primary_image(order: Order) -> str | None:
        if order.items:
            return order.items[0].product_image_url
        return None

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
        joinedload(Order.items)
    ).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id
    ).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

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
        items=order.items,
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


_INVOICEABLE_STATUSES = {"paid", "processing", "shipped", "delivered"}


@router.post("/{order_number}/request-invoice", response_model=Message)
def request_invoice(
    order_number: str,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    """Generate a PDF invoice and send it by email to the authenticated user."""
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(
        Order.order_number == order_number,
        Order.user_id == current_user.id,
    ).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    if order.status not in _INVOICEABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La factura solo está disponible para pedidos pagados",
        )

    customer_name = f"{current_user.first_name} {current_user.last_name}".strip()
    customer_email = current_user.email

    def _send_invoice():
        from app.services.invoice import generate_invoice_pdf
        from app.services.email import send_invoice_email
        pdf_bytes = generate_invoice_pdf(order, customer_name, customer_email)
        send_invoice_email(
            to_email=customer_email,
            first_name=current_user.first_name or customer_name,
            order_number=order_number,
            pdf_bytes=pdf_bytes,
        )

    background_tasks.add_task(_send_invoice)
    return Message(message=f"Factura enviada a {customer_email}")
