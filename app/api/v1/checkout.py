"""
Checkout API endpoints.
"""
from decimal import Decimal
from typing import Optional
import uuid
from datetime import datetime

import stripe as stripe_lib

from fastapi import APIRouter, Depends, HTTPException, Cookie, Header, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, CurrentUserOptional
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.order import Order, OrderItem, Coupon
from app.models.payment import PaymentIntent as PaymentIntentModel
from app.models.user import User, Address
from app.schemas.order import (
    CheckoutCreate, CheckoutValidation, PaymentIntentResponse,
    CompleteCheckout, OrderResponse, ShippingCostResponse
)
from app.services import stripe_service
from app.config import settings
from app.utils.url import normalize_image_url

router = APIRouter()


@router.get("/shipping-cost", response_model=ShippingCostResponse)
async def get_shipping_cost(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = Header(None, alias="X-Cart-Session"),
):
    """Calculate shipping cost for current cart."""
    session_id = cart_session or x_cart_session
    # Get cart
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif session_id:
        cart = db.query(Cart).filter(Cart.session_id == session_id).first()
    
    if not cart:
        subtotal = Decimal('0')
    else:
        cart = db.query(Cart).options(
            joinedload(Cart.items).joinedload(CartItem.product)
        ).filter(Cart.id == cart.id).first()
        subtotal = cart.subtotal
    
    # Calculate discount
    discount = Decimal('0')
    if cart and cart.coupon_code:
        coupon = db.query(Coupon).filter(
            Coupon.code == cart.coupon_code,
            Coupon.is_active == True
        ).first()
        if coupon and coupon.is_valid:
            discount = coupon.calculate_discount(subtotal)
    
    subtotal_after_discount = subtotal - discount
    
    if subtotal_after_discount >= settings.FREE_SHIPPING_THRESHOLD:
        return ShippingCostResponse(
            cost=Decimal('0'),
            free_shipping_threshold=Decimal(str(settings.FREE_SHIPPING_THRESHOLD)),
            amount_for_free_shipping=None,
            message="¡Envío gratis!"
        )
    else:
        remaining = Decimal(str(settings.FREE_SHIPPING_THRESHOLD)) - subtotal_after_discount
        return ShippingCostResponse(
            cost=Decimal(str(settings.SHIPPING_COST)),
            free_shipping_threshold=Decimal(str(settings.FREE_SHIPPING_THRESHOLD)),
            amount_for_free_shipping=remaining,
            message=f"Añade {remaining:.2f}€ más para envío gratis"
        )


@router.post("/validate", response_model=CheckoutValidation)
async def validate_checkout(
    checkout_data: CheckoutCreate,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = Header(None, alias="X-Cart-Session"),
):
    """Validate cart and checkout data before payment."""
    session_id = cart_session or x_cart_session
    errors = []

    # Get cart
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif session_id:
        cart = db.query(Cart).filter(Cart.session_id == session_id).first()
    
    if not cart:
        errors.append("No hay productos en el carrito")
        return CheckoutValidation(
            is_valid=False,
            errors=errors,
            subtotal=Decimal('0'),
            shipping_cost=Decimal('0'),
            discount=Decimal('0'),
            tax=Decimal('0'),
            total=Decimal('0'),
        )
    
    # Load cart items
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product)
    ).filter(Cart.id == cart.id).first()
    
    if not cart.items:
        errors.append("El carrito está vacío")
    
    # Validate stock
    for item in cart.items:
        product = item.product
        if not product.is_active:
            errors.append(f"El producto '{product.name}' ya no está disponible")
        elif not product.is_in_stock:
            errors.append(f"El producto '{product.name}' está agotado")
        elif item.quantity > product.stock:
            errors.append(f"Solo hay {product.stock} unidades de '{product.name}'")
    
    # Validate guest email
    if not current_user and not checkout_data.guest_email:
        errors.append("Se requiere email para compras como invitado")
    
    # Calculate totals
    subtotal = cart.subtotal
    
    # Discount
    discount = Decimal('0')
    if checkout_data.coupon_code or cart.coupon_code:
        coupon_code = checkout_data.coupon_code or cart.coupon_code
        coupon = db.query(Coupon).filter(
            Coupon.code == coupon_code.upper()
        ).first()
        if coupon and coupon.is_valid and subtotal >= coupon.min_order_amount:
            discount = coupon.calculate_discount(subtotal)
    
    subtotal_after_discount = subtotal - discount
    
    # Shipping
    if subtotal_after_discount >= settings.FREE_SHIPPING_THRESHOLD:
        shipping_cost = Decimal('0')
    else:
        shipping_cost = Decimal(str(settings.SHIPPING_COST))
    
    # Tax (IVA included in prices, calculate for display)
    tax_rate = Decimal(str(settings.TAX_RATE))
    tax = (subtotal_after_discount + shipping_cost) * tax_rate / (1 + tax_rate)
    
    total = subtotal_after_discount + shipping_cost
    
    return CheckoutValidation(
        is_valid=len(errors) == 0,
        errors=errors,
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        discount=discount,
        tax=tax.quantize(Decimal('0.01')),
        total=total,
    )


@router.post("/create-payment-intent", response_model=PaymentIntentResponse)
async def create_payment_intent(
    checkout_data: CheckoutCreate,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = Header(None, alias="X-Cart-Session"),
):
    """
    Create Stripe PaymentIntent, persist a pending order, and return client_secret.
    The order transitions to 'paid' via the Stripe webhook after payment succeeds.
    """
    session_id = cart_session or x_cart_session

    # Validate checkout data
    validation = await validate_checkout(checkout_data, db, current_user, cart_session, x_cart_session)
    if not validation.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=validation.errors[0] if validation.errors else "Error de validación"
        )

    # Reload cart with items + product images
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif session_id:
        cart = db.query(Cart).filter(Cart.session_id == session_id).first()

    if not cart:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Carrito no encontrado")

    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()

    # Create Order (status='pending_payment' — stock NOT reduced yet)
    order = Order(
        user_id=current_user.id if current_user else None,
        order_number=Order.generate_order_number(),
        status="pending_payment",
        subtotal=validation.subtotal,
        shipping_cost=validation.shipping_cost,
        discount=validation.discount,
        tax=validation.tax,
        total=validation.total,
        coupon_code=checkout_data.coupon_code or cart.coupon_code,
        guest_email=checkout_data.guest_email if not current_user else None,
        customer_notes=checkout_data.customer_notes,
        payment_method="card",
    )
    order.shipping_address = checkout_data.shipping_address.model_dump()
    if checkout_data.billing_address and not checkout_data.same_billing_address:
        order.billing_address = checkout_data.billing_address.model_dump()

    db.add(order)
    db.flush()  # get order.id

    # Snapshot order items (stock not yet reduced)
    for cart_item in cart.items:
        product = cart_item.product
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            product_image_url=normalize_image_url(product.primary_image),
            quantity=cart_item.quantity,
            unit_price=cart_item.price_at_add,
            total=cart_item.price_at_add * cart_item.quantity,
        )
        db.add(order_item)

    db.flush()  # persist items

    amount_cents = int(validation.total * 100)
    customer_email = current_user.email if current_user else checkout_data.guest_email

    # Create Stripe PaymentIntent
    try:
        intent = stripe_service.create_payment_intent(
            amount=amount_cents,
            currency=settings.STRIPE_CURRENCY,
            order_id=order.id,
            order_number=order.order_number,
            cart_id=cart.id,
            customer_email=customer_email,
        )
    except stripe_lib.StripeError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al conectar con el sistema de pago: {e.user_message or str(e)}"
        )

    # Persist PaymentIntent record
    pi_record = PaymentIntentModel(
        order_id=order.id,
        stripe_payment_intent_id=intent.id,
        stripe_client_secret=intent.client_secret,
        amount=amount_cents,
        currency=settings.STRIPE_CURRENCY,
        status=intent.status,
    )
    db.add(pi_record)

    # Update order with Stripe PI id
    order.payment_intent_id = intent.id

    db.commit()

    return PaymentIntentResponse(
        payment_intent_id=intent.id,
        client_secret=intent.client_secret,
        amount=amount_cents,
        currency=settings.STRIPE_CURRENCY,
        order_number=order.order_number,
    )


@router.post("/complete", response_model=OrderResponse)
async def complete_checkout(
    complete_data: CompleteCheckout,
    db: DbSession,
    current_user: CurrentUserOptional,
):
    """
    Called by the frontend after Stripe.js confirmPayment() returns.
    Verifies the PaymentIntent status directly with Stripe before returning the order.
    Does NOT create a new order — the order was already created in /create-payment-intent.
    The order transitions to 'paid' via the Stripe webhook; this endpoint only confirms
    the PI is succeeded and returns the order for the success page.
    """
    if not complete_data.payment_intent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payment_intent_id requerido"
        )

    # Find the PaymentIntent record in our DB
    pi_record = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == complete_data.payment_intent_id
    ).first()

    if not pi_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro de pago no encontrado"
        )

    # Verify payment status directly with Stripe — never trust frontend claims
    try:
        stripe_pi = stripe_lib.PaymentIntent.retrieve(complete_data.payment_intent_id)
    except stripe_lib.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error al verificar el pago con Stripe"
        )

    if stripe_pi.status not in ("succeeded", "processing"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"El pago no se ha completado (estado: {stripe_pi.status})"
        )

    # Load the order that was created in /create-payment-intent
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.id == pi_record.order_id).first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )

    # Authorization: authenticated user must own the order
    if current_user and order.user_id and order.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso no autorizado"
        )

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


@router.get("/confirmation/{order_number}", response_model=OrderResponse)
async def get_order_confirmation(
    order_number: str,
    db: DbSession,
    current_user: CurrentUserOptional,
    payment_intent: Optional[str] = None,
):
    """
    Public endpoint for the post-payment success page.
    Access is granted if:
    - The user is authenticated and owns the order, OR
    - The payment_intent_id query param matches the order (guest / just-paid flow)
    """
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.order_number == order_number).first()

    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")

    # Authorization: authenticated owner OR valid payment_intent proof
    is_owner = current_user and order.user_id == current_user.id
    has_pi_proof = payment_intent and order.payment_intent_id == payment_intent

    if not is_owner and not has_pi_proof:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso no autorizado")

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
