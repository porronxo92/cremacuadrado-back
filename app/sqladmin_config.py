"""
sqladmin configuration for CremaCuadrado.

Provides:
  - AdminAuth   — authentication backend (DB lookup via verify_password)
  - ModelView subclasses for every entity grouped in 5 categories
"""

# ── Authentication backend ────────────────────────────────────────────────────

from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.models.database import SessionLocal
from app.models.user import User
from app.utils.security import verify_password


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        if not email or not password:
            return False
        db = SessionLocal()
        try:
            user = db.query(User).filter(
                User.email == email,
                User.role == "admin",
                User.is_active == True,
            ).first()
            if user is None or user.password_hash is None:
                return False  # Google-only accounts cannot log in here
            if not verify_password(password, user.password_hash):
                return False
            # Write the token into the session — authenticate() reads it on every request
            request.session["token"] = "authenticated"
            return True
        finally:
            db.close()

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("token"))


# ── ModelViews ────────────────────────────────────────────────────────────────

from markupsafe import Markup
from sqladmin import ModelView

def _img_col(url: str | None) -> Markup:
    """Render a small thumbnail for sqladmin list views."""
    if not url:
        return Markup('<span style="color:#ccc">—</span>')
    return Markup(
        f'<img src="{url}" style="height:48px;max-width:72px;object-fit:cover;'
        f'border-radius:4px;border:1px solid #eee;" '
        f'onerror="this.style.display=\'none\'">'
        f'<small style="display:block;max-width:140px;overflow:hidden;text-overflow:ellipsis;'
        f'white-space:nowrap;color:#888;font-size:0.7em;">{url.split("/")[-1]}</small>'
    )


def _safe_product_thumb(m) -> Markup:
    try:
        return _img_col(m.primary_image)
    except Exception:
        return Markup('<span style="color:#ccc">—</span>')


def _safe_variant_thumb(m) -> Markup:
    try:
        imgs = m.images
        return _img_col(imgs[0].url if imgs else None)
    except Exception:
        return Markup('<span style="color:#ccc">—</span>')

from app.models.user import User, Address, PasswordResetToken
from app.models.product import (
    Category, Product, ProductVariant,
    ProductImage, ProductNutrition, Review,
)
from app.models.cart import Cart, CartItem
from app.models.order import Order, OrderItem, Coupon
from app.models.payment import PaymentIntent, StripeWebhookEvent, Refund
from app.models.blog import BlogPost, BlogCategory
from app.models.point_of_sale import PointOfSale


# ─── Usuarios ────────────────────────────────────────────────────────────────

class UserAdmin(ModelView, model=User):
    name = "Usuario"
    name_plural = "Usuarios"
    icon = "fa-solid fa-users"
    category = "Usuarios"

    column_list = [
        User.id,
        User.email,
        User.first_name,
        User.last_name,
        User.phone,
        User.role,
        User.is_active,
        User.email_verified,
        User.marketing_opt_in,
        User.created_at,
    ]
    column_searchable_list = [User.email, User.first_name, User.last_name]
    column_sortable_list = [User.id, User.email, User.role, User.is_active, User.created_at]
    column_default_sort = [(User.created_at, True)]

    form_excluded_columns = [
        "password_hash",
        "token_version",
        "google_id",
        "created_at",
        "updated_at",
        "addresses",
        "orders",
        "reviews",
        "cart",
        "password_reset_tokens",
    ]


class AddressAdmin(ModelView, model=Address):
    name = "Dirección"
    name_plural = "Direcciones"
    icon = "fa-solid fa-map-pin"
    category = "Usuarios"

    column_list = [
        Address.id,
        Address.user_id,
        Address.label,
        Address.first_name,
        Address.last_name,
        Address.city,
        Address.province,
        Address.postal_code,
        Address.country,
        Address.is_default,
    ]
    column_searchable_list = [Address.city, Address.postal_code, Address.first_name, Address.last_name]
    column_sortable_list = [Address.id, Address.user_id, Address.city, Address.country]

    form_excluded_columns = ["created_at", "user"]


class PasswordResetTokenAdmin(ModelView, model=PasswordResetToken):
    name = "Token Contraseña"
    name_plural = "Tokens de Contraseña"
    icon = "fa-solid fa-key"
    category = "Usuarios"

    column_list = [
        PasswordResetToken.id,
        PasswordResetToken.user_id,
        PasswordResetToken.expires_at,
        PasswordResetToken.used,
        PasswordResetToken.created_at,
    ]
    column_sortable_list = [PasswordResetToken.id, PasswordResetToken.created_at, PasswordResetToken.expires_at]

    form_excluded_columns = ["token", "created_at", "user"]
    can_create = False


# ─── Catálogo ────────────────────────────────────────────────────────────────

class CategoryAdmin(ModelView, model=Category):
    name = "Categoría"
    name_plural = "Categorías"
    icon = "fa-solid fa-tags"
    category = "Catálogo"

    column_list = [
        Category.id,
        Category.slug,
        Category.name,
        "image_url",
        Category.parent_id,
        Category.sort_order,
        Category.is_active,
    ]
    column_searchable_list = [Category.name, Category.slug]
    column_sortable_list = [Category.id, Category.name, Category.sort_order, Category.is_active]
    column_formatters = {
        "image_url": lambda m, a: _img_col(m.image_url),
    }

    form_excluded_columns = ["created_at", "products", "subcategories"]


class ProductAdmin(ModelView, model=Product):
    name = "Producto"
    name_plural = "Productos"
    icon = "fa-solid fa-jar"
    category = "Catálogo"

    column_list = [
        "thumb",
        Product.id,
        Product.sku,
        Product.name,
        Product.category_id,
        Product.price,
        Product.stock,
        Product.is_active,
        Product.is_featured,
        Product.created_at,
    ]
    column_searchable_list = [Product.name, Product.sku, Product.slug]
    column_sortable_list = [
        Product.id, Product.name, Product.price,
        Product.stock, Product.is_active, Product.is_featured, Product.created_at,
    ]
    column_default_sort = [(Product.created_at, True)]
    column_formatters = {
        "thumb": lambda m, a: _safe_product_thumb(m),
    }

    form_excluded_columns = [
        "created_at",
        "updated_at",
        "variants",
        "images",
        "nutrition",
        "reviews",
        "cart_items",
        "order_items",
    ]


class ProductVariantAdmin(ModelView, model=ProductVariant):
    name = "Variante"
    name_plural = "Variantes de Producto"
    icon = "fa-solid fa-cubes"
    category = "Catálogo"

    column_list = [
        "thumb",
        ProductVariant.id,
        ProductVariant.product_id,
        ProductVariant.sku,
        ProductVariant.format,
        ProductVariant.price,
        ProductVariant.compare_price,
        ProductVariant.stock,
        ProductVariant.is_active,
        ProductVariant.sort_order,
    ]
    column_searchable_list = [ProductVariant.sku, ProductVariant.format]
    column_sortable_list = [
        ProductVariant.id, ProductVariant.product_id,
        ProductVariant.price, ProductVariant.stock,
        ProductVariant.sort_order, ProductVariant.is_active,
    ]
    column_formatters = {
        "thumb": lambda m, a: _safe_variant_thumb(m),
    }

    # "images" se incluye en el formulario para poder asociar imágenes existentes
    # al crear o editar una variante. SQLAdmin mostrará un multi-select con las
    # imágenes disponibles (variant_id se actualizará automáticamente).
    form_excluded_columns = ["created_at", "updated_at", "cart_items", "order_items", "product"]


class ProductImageAdmin(ModelView, model=ProductImage):
    name = "Imagen"
    name_plural = "Imágenes de Producto"
    icon = "fa-solid fa-images"
    category = "Catálogo"

    column_list = [
        ProductImage.id,
        ProductImage.product_id,
        ProductImage.variant_id,
        "url",
        ProductImage.alt_text,
        ProductImage.sort_order,
        ProductImage.is_primary,
    ]
    column_searchable_list = [ProductImage.url, ProductImage.alt_text]
    column_sortable_list = [
        ProductImage.id, ProductImage.product_id,
        ProductImage.variant_id, ProductImage.sort_order,
    ]
    column_formatters = {
        "url": lambda m, a: _img_col(m.url),
    }

    form_excluded_columns = ["product", "variant"]


class ProductNutritionAdmin(ModelView, model=ProductNutrition):
    name = "Nutrición"
    name_plural = "Información Nutricional"
    icon = "fa-solid fa-apple-whole"
    category = "Catálogo"

    column_list = [
        ProductNutrition.id,
        ProductNutrition.product_id,
        ProductNutrition.energy_kcal,
        ProductNutrition.fat,
        ProductNutrition.proteins,
        ProductNutrition.carbohydrates,
        ProductNutrition.salt,
    ]
    column_sortable_list = [ProductNutrition.id, ProductNutrition.product_id]

    form_excluded_columns = ["product"]


class ReviewAdmin(ModelView, model=Review):
    name = "Reseña"
    name_plural = "Reseñas"
    icon = "fa-solid fa-star"
    category = "Catálogo"

    column_list = [
        Review.id,
        Review.product_id,
        Review.user_id,
        Review.rating,
        Review.title,
        Review.status,
        Review.is_verified_purchase,
        Review.created_at,
    ]
    column_searchable_list = [Review.title, Review.comment]
    column_sortable_list = [Review.id, Review.rating, Review.status, Review.created_at]
    column_default_sort = [(Review.created_at, True)]

    form_excluded_columns = ["created_at", "updated_at", "product", "user"]


# ─── Pedidos ──────────────────────────────────────────────────────────────────

class OrderAdmin(ModelView, model=Order):
    name = "Pedido"
    name_plural = "Pedidos"
    icon = "fa-solid fa-bag-shopping"
    category = "Pedidos"

    column_list = [
        Order.id,
        Order.order_number,
        Order.user_id,
        Order.guest_email,
        Order.status,
        Order.total,
        Order.payment_method,
        Order.coupon_code,
        Order.tracking_number,
        Order.created_at,
    ]
    column_searchable_list = [Order.order_number, Order.guest_email, Order.tracking_number]
    column_sortable_list = [Order.id, Order.order_number, Order.status, Order.total, Order.created_at]
    column_default_sort = [(Order.created_at, True)]

    form_excluded_columns = [
        "created_at",
        "updated_at",
        "items",
        "payment_intents",
        "user",
    ]

    can_delete = False
    can_create = False


class OrderItemAdmin(ModelView, model=OrderItem):
    name = "Línea de Pedido"
    name_plural = "Líneas de Pedido"
    icon = "fa-solid fa-list-check"
    category = "Pedidos"

    column_list = [
        OrderItem.id,
        OrderItem.order_id,
        OrderItem.product_name,
        OrderItem.product_sku,
        OrderItem.quantity,
        OrderItem.unit_price,
        OrderItem.total,
    ]
    column_searchable_list = [OrderItem.product_name, OrderItem.product_sku]
    column_sortable_list = [OrderItem.id, OrderItem.order_id, OrderItem.quantity, OrderItem.total]

    can_delete = False
    can_create = False
    can_edit = False

    form_excluded_columns = ["order", "product", "variant"]


class CouponAdmin(ModelView, model=Coupon):
    name = "Cupón"
    name_plural = "Cupones"
    icon = "fa-solid fa-ticket"
    category = "Pedidos"

    column_list = [
        Coupon.id,
        Coupon.code,
        Coupon.discount_type,
        Coupon.discount_value,
        Coupon.min_order_amount,
        Coupon.usage_limit,
        Coupon.used_count,
        Coupon.valid_from,
        Coupon.valid_until,
        Coupon.is_active,
    ]
    column_searchable_list = [Coupon.code, Coupon.description]
    column_sortable_list = [Coupon.id, Coupon.code, Coupon.is_active, Coupon.used_count, Coupon.valid_until]

    form_excluded_columns = ["created_at"]


class CartAdmin(ModelView, model=Cart):
    name = "Carrito"
    name_plural = "Carritos"
    icon = "fa-solid fa-cart-shopping"
    category = "Pedidos"

    column_list = [
        Cart.id,
        Cart.user_id,
        Cart.session_id,
        Cart.coupon_code,
        Cart.created_at,
        Cart.updated_at,
    ]
    column_searchable_list = [Cart.session_id, Cart.coupon_code]
    column_sortable_list = [Cart.id, Cart.user_id, Cart.updated_at]
    column_default_sort = [(Cart.updated_at, True)]

    form_excluded_columns = ["created_at", "updated_at", "user", "items"]
    can_create = False


class CartItemAdmin(ModelView, model=CartItem):
    name = "Línea de Carrito"
    name_plural = "Líneas de Carrito"
    icon = "fa-solid fa-basket-shopping"
    category = "Pedidos"

    column_list = [
        CartItem.id,
        CartItem.cart_id,
        CartItem.product_id,
        CartItem.product_variant_id,
        CartItem.quantity,
        CartItem.price_at_add,
    ]
    column_sortable_list = [CartItem.id, CartItem.cart_id, CartItem.quantity]

    form_excluded_columns = ["created_at", "updated_at", "cart", "product", "variant"]


# ─── Pagos ────────────────────────────────────────────────────────────────────

class PaymentIntentAdmin(ModelView, model=PaymentIntent):
    name = "Intento de Pago"
    name_plural = "Intentos de Pago"
    icon = "fa-solid fa-credit-card"
    category = "Pagos"

    column_list = [
        PaymentIntent.id,
        PaymentIntent.order_id,
        PaymentIntent.stripe_payment_intent_id,
        PaymentIntent.amount,
        PaymentIntent.currency,
        PaymentIntent.status,
        PaymentIntent.payment_method_type,
        PaymentIntent.created_at,
    ]
    column_searchable_list = [PaymentIntent.stripe_payment_intent_id]
    column_sortable_list = [
        PaymentIntent.id, PaymentIntent.amount,
        PaymentIntent.status, PaymentIntent.created_at,
    ]
    column_default_sort = [(PaymentIntent.created_at, True)]

    can_delete = False
    can_create = False
    can_edit = False

    form_excluded_columns = [
        "stripe_client_secret",
        "metadata_",
        "created_at",
        "updated_at",
        "order",
    ]


class StripeWebhookEventAdmin(ModelView, model=StripeWebhookEvent):
    name = "Evento Webhook"
    name_plural = "Eventos Webhook Stripe"
    icon = "fa-solid fa-webhook"
    category = "Pagos"

    column_list = [
        StripeWebhookEvent.id,
        StripeWebhookEvent.stripe_event_id,
        StripeWebhookEvent.event_type,
        StripeWebhookEvent.processed,
        StripeWebhookEvent.processed_at,
        StripeWebhookEvent.error,
        StripeWebhookEvent.created_at,
    ]
    column_searchable_list = [StripeWebhookEvent.stripe_event_id, StripeWebhookEvent.event_type]
    column_sortable_list = [
        StripeWebhookEvent.id, StripeWebhookEvent.event_type,
        StripeWebhookEvent.processed, StripeWebhookEvent.created_at,
    ]
    column_default_sort = [(StripeWebhookEvent.created_at, True)]

    can_create = False
    can_delete = False
    can_edit = False

    form_excluded_columns = ["payload", "created_at"]


class RefundAdmin(ModelView, model=Refund):
    name = "Reembolso"
    name_plural = "Reembolsos"
    icon = "fa-solid fa-rotate-left"
    category = "Pagos"

    column_list = [
        Refund.id,
        Refund.order_id,
        Refund.stripe_refund_id,
        Refund.amount,
        Refund.reason,
        Refund.status,
        Refund.created_at,
    ]
    column_searchable_list = [Refund.stripe_refund_id]
    column_sortable_list = [Refund.id, Refund.amount, Refund.status, Refund.created_at]
    column_default_sort = [(Refund.created_at, True)]

    can_create = False
    can_delete = False
    can_edit = False

    form_excluded_columns = ["created_at", "updated_at"]


# ─── Blog ─────────────────────────────────────────────────────────────────────

class BlogCategoryAdmin(ModelView, model=BlogCategory):
    name = "Categoría Blog"
    name_plural = "Categorías Blog"
    icon = "fa-solid fa-folder"
    category = "Blog"

    column_list = [
        BlogCategory.id,
        BlogCategory.slug,
        BlogCategory.name,
        BlogCategory.created_at,
    ]
    column_searchable_list = [BlogCategory.name, BlogCategory.slug]
    column_sortable_list = [BlogCategory.id, BlogCategory.name, BlogCategory.created_at]

    form_excluded_columns = ["created_at", "posts"]


class BlogPostAdmin(ModelView, model=BlogPost):
    name = "Artículo"
    name_plural = "Artículos del Blog"
    icon = "fa-solid fa-newspaper"
    category = "Blog"


# ─── Puntos de Venta ──────────────────────────────────────────────────────────

class PointOfSaleAdmin(ModelView, model=PointOfSale):
    name = "Punto de Venta"
    name_plural = "Puntos de Venta"
    icon = "fa-solid fa-store"
    category = "Puntos de Venta"

    column_list = [
        PointOfSale.id,
        PointOfSale.name,
        PointOfSale.city,
        PointOfSale.is_active,
        PointOfSale.sort_order,
    ]
    column_searchable_list = [PointOfSale.name, PointOfSale.city]
    column_sortable_list = [PointOfSale.id, PointOfSale.city, PointOfSale.sort_order]

    form_excluded_columns = ["created_at"]

    column_list = [
        BlogPost.id,
        "featured_image_url",
        BlogPost.title,
        BlogPost.status,
        BlogPost.author_id,
        BlogPost.published_at,
        BlogPost.created_at,
    ]
    column_searchable_list = [BlogPost.title, BlogPost.slug, BlogPost.excerpt]
    column_sortable_list = [
        BlogPost.id, BlogPost.title, BlogPost.status,
        BlogPost.published_at, BlogPost.created_at,
    ]
    column_default_sort = [(BlogPost.created_at, True)]
    column_formatters = {
        "featured_image_url": lambda m, a: _img_col(m.featured_image_url),
    }

    form_excluded_columns = ["created_at", "updated_at", "author"]
