"""
Checkout API endpoints.
"""
from decimal import Decimal
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Cookie, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, CurrentUserOptional
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.order import Order, OrderItem, Coupon
from app.models.user import User, Address
from app.schemas.order import (
    CheckoutCreate, CheckoutValidation, PaymentIntentResponse,
    CompleteCheckout, OrderResponse, ShippingCostResponse
)
from app.services.email import EmailService
from app.config import settings

router = APIRouter()


@router.get("/shipping-cost", response_model=ShippingCostResponse)
async def get_shipping_cost(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """Calculate shipping cost for current cart."""
    # Get cart
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif cart_session:
        cart = db.query(Cart).filter(Cart.session_id == cart_session).first()
    
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
    cart_session: Optional[str] = Cookie(None)
):
    """Validate cart and checkout data before payment."""
    errors = []
    
    # Get cart
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif cart_session:
        cart = db.query(Cart).filter(Cart.session_id == cart_session).first()
    
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
    cart_session: Optional[str] = Cookie(None)
):
    """
    Create a payment intent for Stripe.
    
    MVP: Returns a mock payment intent.
    Production: Integrate with Stripe API.
    """
    # Validate first
    validation = await validate_checkout(checkout_data, db, current_user, cart_session)
    
    if not validation.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=validation.errors[0] if validation.errors else "Error de validación"
        )
    
    # MVP: Mock payment intent
    payment_intent_id = f"pi_mock_{uuid.uuid4().hex[:24]}"
    client_secret = f"{payment_intent_id}_secret_{uuid.uuid4().hex[:24]}"
    amount_cents = int(validation.total * 100)
    
    # TODO: In production, use Stripe:
    # import stripe
    # stripe.api_key = settings.STRIPE_SECRET_KEY
    # intent = stripe.PaymentIntent.create(
    #     amount=amount_cents,
    #     currency='eur',
    #     metadata={'cart_id': cart.id}
    # )
    
    return PaymentIntentResponse(
        payment_intent_id=payment_intent_id,
        client_secret=client_secret,
        amount=amount_cents,
        currency="eur"
    )


@router.post("/complete", response_model=OrderResponse)
async def complete_checkout(
    complete_data: CompleteCheckout,
    checkout_data: CheckoutCreate,
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None)
):
    """
    Complete checkout and create order after payment.
    
    MVP: Accepts mock payment intent IDs.
    Production: Verify payment with Stripe.
    """
    # Get cart
    cart = None
    if current_user:
        cart = db.query(Cart).filter(Cart.user_id == current_user.id).first()
    elif cart_session:
        cart = db.query(Cart).filter(Cart.session_id == cart_session).first()
    
    if not cart:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No hay productos en el carrito"
        )
    
    # Load cart items
    cart = db.query(Cart).options(
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images)
    ).filter(Cart.id == cart.id).first()
    
    if not cart.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El carrito está vacío"
        )
    
    # Calculate totals
    subtotal = cart.subtotal
    
    # Discount
    discount = Decimal('0')
    coupon_code = checkout_data.coupon_code or cart.coupon_code
    if coupon_code:
        coupon = db.query(Coupon).filter(
            Coupon.code == coupon_code.upper()
        ).first()
        if coupon and coupon.is_valid:
            discount = coupon.calculate_discount(subtotal)
            coupon.used_count += 1
    
    subtotal_after_discount = subtotal - discount
    
    # Shipping
    if subtotal_after_discount >= settings.FREE_SHIPPING_THRESHOLD:
        shipping_cost = Decimal('0')
    else:
        shipping_cost = Decimal(str(settings.SHIPPING_COST))
    
    # Tax
    tax_rate = Decimal(str(settings.TAX_RATE))
    tax = (subtotal_after_discount + shipping_cost) * tax_rate / (1 + tax_rate)
    
    total = subtotal_after_discount + shipping_cost
    
    # Create order
    from datetime import datetime
    order = Order(
        user_id=current_user.id if current_user else None,
        order_number=Order.generate_order_number(),
        status="paid",
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        discount=discount,
        tax=tax.quantize(Decimal('0.01')),
        total=total,
        coupon_code=coupon_code,
        guest_email=checkout_data.guest_email if not current_user else None,
        customer_notes=checkout_data.customer_notes,
        payment_method=complete_data.payment_method,
        payment_intent_id=complete_data.payment_intent_id,
        paid_at=datetime.utcnow(),
    )
    
    # Set addresses
    order.shipping_address = checkout_data.shipping_address.model_dump()
    if checkout_data.billing_address and not checkout_data.same_billing_address:
        order.billing_address = checkout_data.billing_address.model_dump()
    
    db.add(order)
    db.flush()  # Get order.id
    
    # Create order items
    for cart_item in cart.items:
        product = cart_item.product
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            product_sku=product.sku,
            product_image_url=product.primary_image,
            quantity=cart_item.quantity,
            unit_price=cart_item.price_at_add,
            total=cart_item.price_at_add * cart_item.quantity,
        )
        db.add(order_item)
        
        # Reduce stock
        product.stock -= cart_item.quantity
    
    # Save address if requested
    if checkout_data.save_address and current_user:
        addr = checkout_data.shipping_address
        address = Address(
            user_id=current_user.id,
            first_name=addr.first_name,
            last_name=addr.last_name,
            street=addr.street,
            street_2=addr.street_2,
            city=addr.city,
            province=addr.province,
            postal_code=addr.postal_code,
            country=addr.country,
            phone=addr.phone,
            is_default=False,
        )
        db.add(address)
    
    # Clear cart
    db.query(CartItem).filter(CartItem.cart_id == cart.id).delete()
    cart.coupon_code = None
    
    db.commit()
    db.refresh(order)
    
    # Load order items
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.id == order.id).first()
    
    # Send confirmation email
    customer_email = current_user.email if current_user else checkout_data.guest_email
    customer_name = checkout_data.shipping_address.first_name
    items_html = "<ul>"
    for item in order.items:
        items_html += f"<li>{item.product_name} x{item.quantity} - {item.total}€</li>"
    items_html += "</ul>"
    
    EmailService.send_order_confirmation_email(
        to_email=customer_email,
        order_number=order.order_number,
        customer_name=customer_name,
        total=f"{order.total}€",
        items_html=items_html,
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
