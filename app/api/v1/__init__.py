"""API v1 router aggregation."""
from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.products import router as products_router
from app.api.v1.cart import router as cart_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.orders import router as orders_router
from app.api.v1.users import router as users_router
from app.api.v1.blog import router as blog_router
from app.api.v1.admin import router as admin_router
from app.api.v1.admin_blog import router as admin_blog_router
from app.api.v1.admin_catalog import router as admin_catalog_router
from app.api.v1.admin_content import router as admin_content_router
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.admin_insights import router as admin_insights_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.newsletter import router as newsletter_router
from app.api.v1.leads import router as leads_router
from app.api.v1.points_of_sale import router as points_of_sale_router
from app.api.v1.contact import router as contact_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
router.include_router(products_router, prefix="/products", tags=["Products"])
router.include_router(cart_router, prefix="/cart", tags=["Cart"])
router.include_router(checkout_router, prefix="/checkout", tags=["Checkout"])
router.include_router(orders_router, prefix="/orders", tags=["Orders"])
router.include_router(users_router, prefix="/users", tags=["Users"])
router.include_router(blog_router, prefix="/blog", tags=["Blog"])
router.include_router(admin_router, prefix="/admin", tags=["Admin"])
router.include_router(admin_blog_router, prefix="/admin", tags=["Admin Blog"])
router.include_router(admin_catalog_router, prefix="/admin", tags=["Admin Catalog"])
router.include_router(admin_content_router, prefix="/admin", tags=["Admin Content"])
router.include_router(admin_users_router, prefix="/admin", tags=["Admin Users"])
router.include_router(admin_insights_router, prefix="/admin", tags=["Admin Insights"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
router.include_router(newsletter_router, prefix="/newsletter", tags=["Newsletter"])
router.include_router(leads_router, prefix="/leads", tags=["Leads B2B"])
router.include_router(points_of_sale_router, prefix="/points-of-sale", tags=["Points of Sale"])
router.include_router(contact_router, prefix="/contact", tags=["Contact"])
