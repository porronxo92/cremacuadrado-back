"""
Cart API endpoints.
"""
from decimal import Decimal
from typing import Optional
import uuid

from fastapi import APIRouter, HTTPException, Cookie, Response, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, CurrentUserOptional
from app.models.cart import Cart, CartItem
from app.models.product import Product, ProductVariant
from app.models.order import Coupon
from app.models.user import User
from app.schemas.cart import (
    CartResponse, CartItemCreate, CartItemUpdate, CartItemResponse,
    ApplyCoupon, CouponInfo
)
from app.schemas.common import Message
from app.config import settings
from app.utils.url import normalize_image_url

router = APIRouter()


def set_cart_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key="cart_session",
        value=session_id,
        httponly=True,
        max_age=60 * 60 * 24 * 30,
        samesite="none",
        secure=True,
    )


def get_or_create_cart(db: Session, user: Optional[User] = None, session_id: Optional[str] = None) -> Cart:
    cart = None
    if user:
        cart = db.query(Cart).filter(Cart.user_id == user.id).first()
        if not cart and session_id:
            session_cart = db.query(Cart).filter(Cart.session_id == session_id).first()
            if session_cart:
                session_cart.user_id = user.id
                session_cart.session_id = None
                db.commit()
                cart = session_cart
    elif session_id:
        cart = db.query(Cart).filter(Cart.session_id == session_id).first()

    if not cart:
        cart = Cart(
            user_id=user.id if user else None,
            session_id=session_id if not user else None
        )
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def calculate_shipping(subtotal: Decimal) -> tuple[Decimal, Optional[str]]:
    threshold = Decimal(str(settings.FREE_SHIPPING_THRESHOLD))
    if subtotal >= threshold:
        return Decimal('0'), "Envio gratis!"
    remaining = threshold - subtotal
    return Decimal(str(settings.SHIPPING_COST)), f"Añade {remaining:.2f}€ más para envío gratis"


def _load_cart(db: Session, cart_id: int) -> Cart:
    return db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.variant).joinedload(ProductVariant.product).joinedload(Product.images),
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images),
    ).filter(Cart.id == cart_id).first()


def cart_to_response(cart: Cart, db: Session) -> CartResponse:
    items = []
    subtotal = Decimal('0')

    for item in cart.items:
        variant = item.variant
        product = item.product
        item_total = item.price_at_add * item.quantity
        subtotal += item_total

        items.append(CartItemResponse(
            id=item.id,
            product_id=product.id,
            product_variant_id=variant.id,
            product_name=product.name,
            product_slug=product.slug,
            variant_format=variant.format,
            product_image=normalize_image_url(product.primary_image),
            unit_price=variant.price,
            price_at_add=item.price_at_add,
            quantity=item.quantity,
            total=item_total,
            is_available=variant.is_in_stock and variant.is_active and product.is_active,
            stock_available=variant.stock,
        ))

    discount = Decimal('0')
    coupon_info = None
    if cart.coupon_code:
        coupon = db.query(Coupon).filter(
            Coupon.code == cart.coupon_code, Coupon.is_active == True
        ).first()
        if coupon and coupon.is_valid:
            discount = coupon.calculate_discount(subtotal)
            coupon_info = CouponInfo(
                code=coupon.code,
                discount_type=coupon.discount_type,
                discount_value=coupon.discount_value,
                discount_amount=discount,
            )

    shipping_cost, shipping_message = calculate_shipping(subtotal - discount)

    return CartResponse(
        id=cart.id,
        item_count=sum(item.quantity for item in cart.items),
        items=items,
        subtotal=subtotal,
        coupon=coupon_info,
        discount=discount,
        shipping_cost=shipping_cost,
        shipping_message=shipping_message,
        total=subtotal - discount + shipping_cost,
        updated_at=cart.updated_at,
    )


@router.get("", response_model=CartResponse)
async def get_cart(
    db: DbSession,
    current_user: CurrentUserOptional,
    response: Response,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    # Accept cart_session from both cookie and header (for cross-domain scenarios)
    session_id = cart_session or x_cart_session
    if not current_user and not session_id:
        session_id = str(uuid.uuid4())
        set_cart_cookie(response, session_id)
        response.headers["X-Cart-Session"] = session_id

    cart = get_or_create_cart(db, current_user, session_id)
    return cart_to_response(_load_cart(db, cart.id), db)


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    item_data: CartItemCreate,
    db: DbSession,
    current_user: CurrentUserOptional,
    response: Response,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    """Add a product variant to the cart."""
    # Accept cart_session from both cookie and header
    session_id = cart_session or x_cart_session
    if not current_user and not session_id:
        session_id = str(uuid.uuid4())
        set_cart_cookie(response, session_id)
        response.headers["X-Cart-Session"] = session_id

    variant = db.query(ProductVariant).options(
        joinedload(ProductVariant.product)
    ).filter(
        ProductVariant.id == item_data.product_variant_id,
        ProductVariant.is_active == True,
    ).first()

    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variante no encontrada")

    if not variant.product.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Producto no disponible")

    if not variant.is_in_stock:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato agotado")

    if item_data.quantity > variant.stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo hay {variant.stock} unidades disponibles"
        )

    cart = get_or_create_cart(db, current_user, session_id)

    existing = db.query(CartItem).filter(
        CartItem.cart_id == cart.id,
        CartItem.product_variant_id == variant.id,
    ).first()

    if existing:
        new_qty = existing.quantity + item_data.quantity
        if new_qty > variant.stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Solo hay {variant.stock} unidades disponibles"
            )
        existing.quantity = new_qty
    else:
        db.add(CartItem(
            cart_id=cart.id,
            product_id=variant.product_id,
            product_variant_id=variant.id,
            quantity=item_data.quantity,
            price_at_add=variant.price,
        ))

    db.commit()
    return cart_to_response(_load_cart(db, cart.id), db)


@router.put("/items/{item_id}", response_model=CartResponse)
async def update_cart_item(
    item_id: int,
    item_data: CartItemUpdate,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    session_id = cart_session or x_cart_session
    cart = get_or_create_cart(db, current_user, session_id)
    cart_item = db.query(CartItem).options(
        joinedload(CartItem.variant)
    ).filter(
        CartItem.id == item_id, CartItem.cart_id == cart.id
    ).first()

    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado en el carrito")

    if item_data.quantity > cart_item.variant.stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo hay {cart_item.variant.stock} unidades disponibles"
        )

    cart_item.quantity = item_data.quantity
    db.commit()
    return cart_to_response(_load_cart(db, cart.id), db)


@router.delete("/items/{item_id}", response_model=CartResponse)
async def remove_cart_item(
    item_id: int,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    session_id = cart_session or x_cart_session
    cart = get_or_create_cart(db, current_user, session_id)
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id, CartItem.cart_id == cart.id
    ).first()

    if not cart_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item no encontrado en el carrito")

    db.delete(cart_item)
    db.commit()
    return cart_to_response(_load_cart(db, cart.id), db)


@router.delete("", response_model=Message)
async def clear_cart(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    session_id = cart_session or x_cart_session
    cart = get_or_create_cart(db, current_user, session_id)
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    cart.coupon_code = None
    db.commit()
    return Message(message="Carrito vaciado")


@router.post("/apply-coupon", response_model=CartResponse)
async def apply_coupon(
    coupon_data: ApplyCoupon,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    session_id = cart_session or x_cart_session
    cart = get_or_create_cart(db, current_user, session_id)
    cart = _load_cart(db, cart.id)

    coupon = db.query(Coupon).filter(Coupon.code == coupon_data.code.upper()).first()
    if not coupon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cupon no encontrado")
    if not coupon.is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cupon no valido o expirado")

    subtotal = cart.subtotal
    if subtotal < coupon.min_order_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El pedido minimo para este cupon es {coupon.min_order_amount}€"
        )

    cart.coupon_code = coupon.code
    db.commit()
    return cart_to_response(cart, db)


@router.delete("/remove-coupon", response_model=CartResponse)
async def remove_coupon(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = None,
):
    session_id = cart_session or x_cart_session
    cart = get_or_create_cart(db, current_user, session_id)
    cart.coupon_code = None
    db.commit()
    return cart_to_response(_load_cart(db, cart.id), db)
