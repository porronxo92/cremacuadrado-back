"""
Checkout API endpoints.
"""
from decimal import Decimal
from typing import Optional
import logging
import uuid
from datetime import datetime

import stripe as stripe_lib

from fastapi import APIRouter, Depends, HTTPException, Cookie, Header, Request, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, CurrentUserOptional
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.order import Order, OrderItem, Coupon
from app.models.payment import PaymentIntent as PaymentIntentModel
from app.models.user import User, Address
from app.schemas.order import (
    CheckoutPreConfirm, CheckoutCreate, CheckoutValidation, PaymentIntentResponse,
    CompleteCheckout, OrderResponse, ShippingCostResponse
)
from app.schemas.common import Message
from app.services import consents, stripe_service
from app.services.coupon_redemptions import user_coupon_error
from app.services.pricing import compute_totals, shipping_zone
from app.limiter import limiter
from app.utils.site import request_site_url
from app.config import settings
from app.utils.url import normalize_image_url

logger = logging.getLogger("cremacuadrado.checkout")
router = APIRouter()

QUOTE_ZONE_ERROR = (
    "No podemos cobrar envíos a Canarias, Ceuta ni Melilla desde la web: hay trámites de "
    "aduana y calculamos el envío a medida. Pídenos presupuesto por WhatsApp."
)


@router.get("/shipping-cost", response_model=ShippingCostResponse)
async def get_shipping_cost(
    db: DbSession,
    current_user: CurrentUserOptional,
    cart_session: Optional[str] = Cookie(None),
    x_cart_session: Optional[str] = Header(None, alias="X-Cart-Session"),
    postal_code: Optional[str] = None,
):
    """Calculate shipping cost for current cart (por zona si se indica el CP)."""
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
    
    # Los cupones solo aplican a carritos de usuarios con sesión
    coupon = None
    if cart and cart.coupon_code and current_user:
        coupon = db.query(Coupon).filter(
            Coupon.code == cart.coupon_code,
            Coupon.is_active == True
        ).first()
        if coupon and (not coupon.is_valid or (coupon.user_id is not None and coupon.user_id != current_user.id)):
            coupon = None

    zone = shipping_zone(postal_code)
    totals = compute_totals(subtotal, coupon, zone)
    threshold = Decimal(str(settings.FREE_SHIPPING_THRESHOLD))
    # Solo la península tiene umbral de envío gratis
    free = zone != "peninsula" or totals.shipping_cost == 0
    return ShippingCostResponse(
        cost=totals.shipping_cost,
        free_shipping_threshold=threshold,
        amount_for_free_shipping=None if free else threshold - (subtotal - totals.discount),
        message=totals.shipping_message,
        shipping_zone=zone,
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
        joinedload(Cart.items).joinedload(CartItem.product),
        joinedload(Cart.items).joinedload(CartItem.variant),
    ).filter(Cart.id == cart.id).first()

    if not cart.items:
        errors.append("El carrito está vacío")

    # Validate stock against variant (not legacy product.stock)
    for item in cart.items:
        product = item.product
        variant = item.variant
        if not product.is_active:
            errors.append(f"El producto '{product.name}' ya no está disponible")
        elif not variant or not variant.is_active:
            errors.append(f"El formato seleccionado de '{product.name}' ya no está disponible")
        elif not variant.is_in_stock:
            errors.append(f"El formato {variant.format} de '{product.name}' está agotado")
        elif item.quantity > variant.stock:
            errors.append(f"Solo hay {variant.stock} unidades del formato {variant.format} de '{product.name}'")
        # Se cobra el precio vigente, no el de cuando se añadió al carrito
        if variant and item.price_at_add != variant.price:
            item.price_at_add = variant.price
    
    # Validate guest email
    if not current_user and not checkout_data.guest_email:
        errors.append("Se requiere email para compras como invitado")

    # Ámbito de envío: España peninsular y Baleares; el resto, presupuesto por WhatsApp
    address = checkout_data.shipping_address
    zone = shipping_zone(address.postal_code)
    if address.country not in settings.SHIPPING_ALLOWED_COUNTRIES:
        errors.append("De momento solo enviamos a España")
    elif zone == "quote":
        errors.append(QUOTE_ZONE_ERROR)
    
    # Calculate totals
    subtotal = cart.subtotal
    
    # Discount — coupons are a logged-in benefit only, so they can't be used
    # by guests even if a code is passed directly in the request body.
    coupon = None
    if checkout_data.coupon_code or cart.coupon_code:
        if not current_user:
            errors.append("Inicia sesión o crea una cuenta para utilizar cupones de descuento")
        else:
            coupon_code = checkout_data.coupon_code or cart.coupon_code
            coupon = db.query(Coupon).filter(
                Coupon.code == coupon_code.upper()
            ).first()
            if not coupon or not coupon.is_valid:
                errors.append("Cupón no válido o expirado")
                coupon = None
            elif (user_error := user_coupon_error(db, coupon, current_user.id)):
                errors.append(user_error)
                coupon = None

    # Un cupón que no aplica (mínimo, envío ya gratis) no bloquea: simplemente no descuenta
    totals = compute_totals(subtotal, coupon, zone)
    discount = totals.discount
    shipping_cost = totals.shipping_cost
    subtotal_after_discount = subtotal - discount

    # Tax (IVA included in prices, calculate for display)
    tax_rate = Decimal(str(settings.TAX_RATE))
    tax = (subtotal_after_discount + shipping_cost) * tax_rate / (1 + tax_rate)

    total = totals.total

    return CheckoutValidation(
        is_valid=len(errors) == 0,
        errors=errors,
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        shipping_discount=totals.shipping_discount,
        discount=discount,
        tax=tax.quantize(Decimal('0.01')),
        total=total,
        shipping_zone=zone,
    )


@router.post("/create-payment-intent", response_model=PaymentIntentResponse)
@limiter.limit("10/minute")
async def create_payment_intent(
    checkout_data: CheckoutCreate,
    request: Request,
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
        joinedload(Cart.items).joinedload(CartItem.product).joinedload(Product.images),
        joinedload(Cart.items).joinedload(CartItem.variant),
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
        tax_rate=Decimal(str(settings.TAX_RATE)),
        site_url=request_site_url(request),
        total=validation.total,
        # Solo se registra el cupón si ha aplicado (descuento o envío anulado)
        coupon_code=((checkout_data.coupon_code or cart.coupon_code or '').upper() or None) if (validation.discount > 0 or validation.shipping_discount > 0) else None,
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
        variant = cart_item.variant
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_variant_id=cart_item.product_variant_id,
            product_name=product.name,
            product_sku=(variant.sku if variant and variant.sku else product.sku),
            product_image_url=normalize_image_url(product.primary_image),
            quantity=cart_item.quantity,
            unit_price=cart_item.price_at_add,
            total=cart_item.price_at_add * cart_item.quantity,
        )
        db.add(order_item)

    db.flush()  # persist items

    amount_cents = int((validation.total * 100).to_integral_value())
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
        logger.error(
            "Stripe PaymentIntent creation failed: order=%s amount=%s error=%s",
            order.order_number, amount_cents, e, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al conectar con el sistema de pago: {e.user_message or str(e)}"
        )

    # Persist PaymentIntent record — store cart_id in metadata so the
    # payment webhook can clear the correct cart on fulfillment.
    pi_record = PaymentIntentModel(
        order_id=order.id,
        stripe_payment_intent_id=intent.id,
        stripe_client_secret=intent.client_secret,
        amount=amount_cents,
        currency=settings.STRIPE_CURRENCY,
        status=intent.status,
        metadata_={"cart_id": str(cart.id)},
    )
    db.add(pi_record)

    # Update order with Stripe PI id
    order.payment_intent_id = intent.id

    db.commit()

    logger.info(
        "PaymentIntent created: order=%s pi=%s amount=%s",
        order.order_number, intent.id, amount_cents,
    )

    return PaymentIntentResponse(
        payment_intent_id=intent.id,
        client_secret=intent.client_secret,
        amount=amount_cents,
        currency=settings.STRIPE_CURRENCY,
        order_number=order.order_number,
    )


class PreConfirmResponse(Message):
    """Mensaje + importes finales (el envío puede cambiar al cambiar de zona)."""
    total: Optional[Decimal] = None
    shipping_cost: Optional[Decimal] = None


REPRICE_ERROR = "No hemos podido actualizar el importe del pedido. Inténtalo de nuevo."


def _sync_order_coupon(db: Session, order: Order) -> bool:
    """
    El cliente registrado puede aplicar o quitar el cupón en el propio checkout, después
    de que se creara el pedido. Antes de cobrar, el pedido toma el cupón que tenga ahora
    su cesta, con las mismas reglas que validate (vigente y no usado ya por el cliente).
    Los invitados no tienen cupones. Devuelve True si el cupón del pedido ha cambiado.
    """
    if order.user_id is None:
        return False
    cart = db.query(Cart).filter(Cart.user_id == order.user_id).first()
    code = ((cart.coupon_code if cart else None) or "").upper() or None
    if code:
        coupon = db.query(Coupon).filter(Coupon.code == code).first()
        if not coupon or not coupon.is_valid or user_coupon_error(db, coupon, order.user_id):
            code = None
    if code == ((order.coupon_code or "").upper() or None):
        return False
    order.coupon_code = code
    return True


def _reprice_for_address(db: Session, order: Order, postal_code: Optional[str]) -> None:
    """
    El pedido se creó con la dirección y el cupón de ese momento. Si la zona de la
    dirección final o el cupón cambian el importe, recalcula pedido y PaymentIntent
    (mismo cálculo que validate). Si Stripe falla no se guarda nada.
    """
    coupon = None
    if order.coupon_code:
        coupon = db.query(Coupon).filter(Coupon.code == order.coupon_code.upper()).first()
        if coupon and not coupon.is_valid:
            coupon = None
    totals = compute_totals(order.subtotal, coupon, shipping_zone(postal_code))
    # Como al crear el pedido: solo se registra el cupón si aplica
    if not coupon or (totals.discount <= 0 and totals.shipping_discount <= 0):
        order.coupon_code = None
    if (totals.shipping_cost == order.shipping_cost and totals.total == order.total
            and totals.discount == order.discount):
        return

    pi_id = order.payment_intent_id
    try:
        pi = stripe_service.retrieve_payment_intent(pi_id)
    except stripe_lib.StripeError:
        logger.error("pre-confirm: no se pudo leer el PI %s", pi_id, exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=REPRICE_ERROR)
    if pi.status not in stripe_service.MODIFIABLE_PI_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El pago de este pedido ya está en curso y no se puede cambiar el importe. "
                   "Vuelve al carrito para empezar un pedido nuevo.",
        )

    amount_cents = int((totals.total * 100).to_integral_value())
    try:
        stripe_service.modify_payment_intent_amount(pi_id, amount_cents)
    except stripe_lib.StripeError:
        db.rollback()
        logger.error("pre-confirm: Stripe no modificó el PI %s", pi_id, exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=REPRICE_ERROR)

    tax_rate = Decimal(str(order.tax_rate if order.tax_rate is not None else settings.TAX_RATE))
    order.discount = totals.discount
    order.shipping_cost = totals.shipping_cost
    order.total = totals.total
    order.tax = (totals.total * tax_rate / (1 + tax_rate)).quantize(Decimal("0.01"))
    pi_record = db.query(PaymentIntentModel).filter(
        PaymentIntentModel.stripe_payment_intent_id == pi_id).first()
    if pi_record:
        pi_record.amount = amount_cents


@router.post("/pre-confirm", response_model=PreConfirmResponse)
def pre_confirm_order(data: CheckoutPreConfirm, request: Request, db: DbSession):
    """
    Se llama justo antes de confirmar el pago en Stripe. Registra la aceptación
    expresa de las condiciones de venta (versión, fecha e IP: TRLGDCU art. 98)
    y guarda los datos fiscales si se pide factura con NIF.
    Autorización: el payment_intent_id del pedido, que solo conoce quien lo creó.
    """
    if not data.accept_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes aceptar las condiciones generales de venta",
        )
    order = db.query(Order).filter(Order.order_number == data.order_number).first()
    if not order or order.payment_intent_id != data.payment_intent_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido no encontrado")
    if order.status != "pending_payment":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El pedido ya está pagado; los datos de facturación no se pueden cambiar",
        )
    coupon_changed = _sync_order_coupon(db, order)
    if data.shipping_address is not None:
        address = data.shipping_address
        if address.country not in settings.SHIPPING_ALLOWED_COUNTRIES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="De momento solo enviamos a España")
        if shipping_zone(address.postal_code) == "quote":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=QUOTE_ZONE_ERROR)
        _reprice_for_address(db, order, address.postal_code)
        order.shipping_address = address.model_dump()
    elif coupon_changed:
        _reprice_for_address(db, order, (order.shipping_address or {}).get("postal_code"))
    if data.guest_email and order.user_id is None:
        order.guest_email = str(data.guest_email).lower()
    order.billing_address = data.billing.model_dump() if data.billing else None
    order.terms_version = settings.TERMS_VERSION
    order.terms_accepted_at = datetime.utcnow()
    order.acceptance_ip = consents.client_ip(request)
    if data.terms_version and data.terms_version != settings.TERMS_VERSION:
        logger.warning("Terms version mismatch: order=%s client=%s server=%s",
                       order.order_number, data.terms_version, settings.TERMS_VERSION)
    db.commit()
    return PreConfirmResponse(message="Pedido listo para el pago", total=order.total,
                              shipping_cost=order.shipping_cost)


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
        logger.warning("complete_checkout: PaymentIntent record not found pi=%s", complete_data.payment_intent_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro de pago no encontrado"
        )

    # Verify payment status directly with Stripe — never trust frontend claims
    try:
        stripe_lib.api_key = settings.STRIPE_SECRET_KEY
        stripe_pi = stripe_lib.PaymentIntent.retrieve(complete_data.payment_intent_id)
    except stripe_lib.StripeError as e:
        logger.error(
            "complete_checkout: Stripe retrieve failed pi=%s error=%s",
            complete_data.payment_intent_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error al verificar el pago con Stripe"
        )

    if stripe_pi.status not in ("succeeded", "processing"):
        logger.warning(
            "complete_checkout: payment not completed pi=%s status=%s",
            complete_data.payment_intent_id, stripe_pi.status,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"El pago no se ha completado (estado: {stripe_pi.status})"
        )

    # Load the order that was created in /create-payment-intent
    order = db.query(Order).options(
        joinedload(Order.items)
    ).filter(Order.id == pi_record.order_id).first()

    if not order:
        logger.error("complete_checkout: order not found for pi_record.order_id=%s", pi_record.order_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pedido no encontrado"
        )

    # Authorization: authenticated user must own the order
    if current_user and order.user_id and order.user_id != current_user.id:
        logger.warning(
            "complete_checkout: unauthorized access attempt order=%s by user=%s",
            order.order_number, current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso no autorizado"
        )

    logger.info("Order confirmed: order=%s pi=%s status=%s", order.order_number, complete_data.payment_intent_id, stripe_pi.status)

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

    # Fallback: if the order is still pending_payment but Stripe has already charged
    # the customer, process the fulfillment inline.
    # This covers: (a) local dev without Stripe CLI, (b) delayed webhook delivery.
    if order.status == "pending_payment" and order.payment_intent_id:
        try:
            stripe_lib.api_key = settings.STRIPE_SECRET_KEY
            stripe_pi = stripe_lib.PaymentIntent.retrieve(order.payment_intent_id)
            if stripe_pi.status == "succeeded":
                logger.info(
                    "confirmation fallback: processing succeeded PI inline order=%s pi=%s",
                    order_number, order.payment_intent_id,
                )
                from app.api.v1.webhooks import handle_payment_succeeded
                handle_payment_succeeded(db, order.payment_intent_id)
                db.commit()  # the webhook dispatcher commits for itself; this path must too
                db.refresh(order)
        except Exception as exc:
            logger.warning(
                "confirmation fallback failed: order=%s error=%s",
                order_number, exc, exc_info=True,
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
