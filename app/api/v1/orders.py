"""
Orders API endpoints.
"""
from typing import List

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import joinedload

from app.api.deps import DbSession, CurrentUser
from app.models.order import Order, OrderItem
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.schemas.order import OrderResponse, OrderListResponse
from app.schemas.common import Message, PaginatedResponse
from app.config import settings

router = APIRouter()


@router.get("/", response_model=PaginatedResponse[OrderListResponse])
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

    return PaginatedResponse.create(
        [
            OrderListResponse(
                id=order.id,
                order_number=order.order_number,
                status=order.status,
                total=order.total,
                item_count=order.item_count,
                created_at=order.created_at,
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

    # Batch-load all products and existing cart items in 2 queries instead of N
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

    cart_items_by_product = {
        ci.product_id: ci for ci in db.query(CartItem).filter(
            CartItem.cart_id == cart.id,
            CartItem.product_id.in_(product_ids),
        ).all()
    }

    added_count = 0
    unavailable = []

    for order_item in order.items:
        if not order_item.product_id:
            unavailable.append(order_item.product_name)
            continue

        product = products_by_id.get(order_item.product_id)
        if not product:
            unavailable.append(order_item.product_name)
            continue

        if not product.is_in_stock:
            unavailable.append(product.name)
            continue

        quantity_to_add = min(order_item.quantity, product.stock)
        existing_item = cart_items_by_product.get(product.id)

        if existing_item:
            existing_item.quantity = min(existing_item.quantity + quantity_to_add, product.stock)
        else:
            db.add(CartItem(
                cart_id=cart.id,
                product_id=product.id,
                quantity=quantity_to_add,
                price_at_add=product.price,
            ))

        added_count += 1

    db.commit()

    if unavailable:
        message = f"Se añadieron {added_count} productos. No disponibles: {', '.join(unavailable)}"
    else:
        message = f"Se añadieron {added_count} productos al carrito"

    return Message(message=message)
