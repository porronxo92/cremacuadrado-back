"""
Cart API endpoints.
"""
from decimal import Decimal
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Cookie, Response, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, CurrentUserOptional
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.order import Coupon
from app.models.user import User
from app.schemas.cart import (
    CartResponse, CartItemCreate, CartItemUpdate, CartItemResponse,
    ApplyCoupon, CouponInfo
)
from app.schemas.common import Message
from app.config import settings

router = APIRouter()


def set_cart_cookie(response: Response, session_id: str) -> None:
    """Set cart session cookie with appropriate settings."""
    # In development, use less restrictive settings for cross-origin requests
    response.set_cookie(
        key="cart_session",
        value=session_id,
        httponly=True,
        max_age=60 * 60 * 24 * 30,  # 30 days
        samesite="none" if settings.DEBUG else "lax",
        secure=not settings.DEBUG,  # Secure only in production
    )


def get_or_create_cart(
    db: Session,
    user: Optional[User] = None,
    session_id: Optional[str] = None
) -> Cart:
    """Get existing cart or create a new one."""
    cart = None
    
    if user:
        # Try to find user's cart
        cart = db.query(Cart).filter(Cart.user_id == user.id).first()
        
        # If user has a session cart, merge it
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
    """Calculate shipping cost and message."""
    threshold = Decimal(str(settings.FREE_SHIPPING_THRESHOLD))
    if subtotal >= threshold:
        return Decimal('0'), "¡Envío gratis!"
    else:
        remaining = threshold - subtotal
        message = f"Añade {remaining:.2f}€ más para envío gratis"
        return Decimal(str(settings.SHIPPING_COST)), message


def cart_to_response(cart: Cart, db: Session) -> CartResponse:
    """Convert Cart model to CartResponse."""
    items = []
    subtotal = Decimal('0')
    
    for item in cart.items:
        product = item.product
        item_total = item.price_at_add * item.quantity
        subtotal += item_total
        
        items.append(CartItemResponse(
            id=item.id,
            product_id=product.id,
            product_name=product.name,
            product_slug=product.slug,
            product_image=product.primary_image,
            product_price=product.price,
            quantity=item.quantity,
            price_at_add=item.price_at_add,
            total=item_total,
            is_available=product.is_in_stock and product.is_active,
            stock_available=product.stock,
        ))
    
    # Calculate discount if coupon
    discount = Decimal('0')
    coupon_info = None
    if cart.coupon_code:
        coupon = db.query(Coupon).filter(
            Coupon.code == cart.coupon_code,
            Coupon.is_active == True
        ).first()
        if coupon and coupon.is_valid:
            discount = coupon.calculate_discount(subtotal)
            coupon_info = CouponInfo(
                code=coupon.code,
                discount_type=coupon.discount_type,
                discount_value=coupon.discount_value,
                discount_amount=discount,
            )
    
    # Calculate shipping
    subtotal_after_discount = subtotal - discount
    shipping_cost, shipping_message = calculate_shipping(subtotal_after_discount)
    
    total = subtotal_after_discount + shipping_cost
    
    return CartResponse(
        id=cart.id,
        item_count=sum(item.quantity for item in cart.items),
        items=items,
        subtotal=subtotal,
        coupon=coupon_info,
        discount=discount,
        shipping_cost=shipping_cost,
        shipping_message=shipping_message,
        total=total,
        updated_at=cart.updated_at,
    )


@router.get("", response_model=CartResponse)
async def get_cart(
    db: DbSession,
    current_user: CurrentUserOptional,
    response: Response,
    cart_session: Optional[str] = Cookie(None)
):
    """Get current cart."""
    # Generate session ID for guests
    session_id = cart_session
    if not current_user and not session_id:
        session_id = str(uuid.uuid4())
        set_cart_cookie(response, session_id)

    cart = get_or_create_cart(db, current_user, session_id)
    
    # Load relationships
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    return cart_to_response(cart, db)


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED)
async def add_to_cart(
    item_data: CartItemCreate,
    db: DbSession,
    current_user: CurrentUserOptional,
    response: Response,
    cart_session: Optional[str] = Cookie(None)
):
    """Add item to cart."""
    # Generate session ID for guests
    session_id = cart_session
    if not current_user and not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie(
            key="cart_session",
            value=session_id,
            httponly=True,
            max_age=60 * 60 * 24 * 30,
            samesite="lax"
        )
    
    # Get product
    product = db.query(Product).filter(
        Product.id == item_data.product_id,
        Product.is_active == True
    ).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado"
        )
    
    if not product.is_in_stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Producto agotado"
        )
    
    if item_data.quantity > product.stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo hay {product.stock} unidades disponibles"
        )
    
    # Get or create cart
    cart = get_or_create_cart(db, current_user, session_id)
    
    # Check if product already in cart
    existing_item = db.query(CartItem).filter(
        CartItem.cart_id == cart.id,
        CartItem.product_id == product.id
    ).first()
    
    if existing_item:
        # Update quantity
        new_quantity = existing_item.quantity + item_data.quantity
        if new_quantity > product.stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Solo hay {product.stock} unidades disponibles"
            )
        existing_item.quantity = new_quantity
    else:
        # Add new item
        cart_item = CartItem(
            cart_id=cart.id,
            product_id=product.id,
            quantity=item_data.quantity,
            price_at_add=product.price,
        )
        db.add(cart_item)
    
    db.commit()
    
    # Reload cart with relationships
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    return cart_to_response(cart, db)


@router.put("/items/{item_id}", response_model=CartResponse)
async def update_cart_item(
    item_id: int,
    item_data: CartItemUpdate,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Update cart item quantity."""
    cart = get_or_create_cart(db, current_user, cart_session)
    
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.cart_id == cart.id
    ).first()
    
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item no encontrado en el carrito"
        )
    
    # Check stock
    if item_data.quantity > cart_item.product.stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Solo hay {cart_item.product.stock} unidades disponibles"
        )
    
    cart_item.quantity = item_data.quantity
    db.commit()
    
    # Reload cart
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    return cart_to_response(cart, db)


@router.delete("/items/{item_id}", response_model=CartResponse)
async def remove_cart_item(
    item_id: int,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Remove item from cart."""
    cart = get_or_create_cart(db, current_user, cart_session)
    
    cart_item = db.query(CartItem).filter(
        CartItem.id == item_id,
        CartItem.cart_id == cart.id
    ).first()
    
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item no encontrado en el carrito"
        )
    
    db.delete(cart_item)
    db.commit()
    
    # Reload cart
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    return cart_to_response(cart, db)


@router.delete("", response_model=Message)
async def clear_cart(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Clear all items from cart."""
    cart = get_or_create_cart(db, current_user, cart_session)
    
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    cart.coupon_code = None
    db.commit()
    
    return Message(message="Carrito vaciado")


@router.post("/apply-coupon", response_model=CartResponse)
async def apply_coupon(
    coupon_data: ApplyCoupon,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Apply coupon to cart."""
    cart = get_or_create_cart(db, current_user, cart_session)
    
    # Load cart items
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    # Find coupon
    coupon = db.query(Coupon).filter(
        Coupon.code == coupon_data.code.upper()
    ).first()
    
    if not coupon:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cupón no encontrado"
        )
    
    if not coupon.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cupón no válido o expirado"
        )
    
    # Check minimum order
    subtotal = cart.subtotal
    if subtotal < coupon.min_order_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El pedido mínimo para este cupón es {coupon.min_order_amount}€"
        )
    
    # Apply coupon
    cart.coupon_code = coupon.code
    db.commit()
    
    return cart_to_response(cart, db)


@router.delete("/remove-coupon", response_model=CartResponse)
async def remove_coupon(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Remove coupon from cart."""
    cart = get_or_create_cart(db, current_user, cart_session)
    
    cart.coupon_code = None
    db.commit()
    
    # Reload cart
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    return cart_to_response(cart, db)
