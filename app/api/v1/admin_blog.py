"""
Admin API endpoints — Blog & recipe content management (CMS).

Recipes are just BlogPost rows tagged with the "recetas" category; there is
no separate recipe model.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from slugify import slugify
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession, AdminUser
from app.models.blog import BlogPost, BlogCategory
from app.schemas.blog import (
    BlogPostCreate, BlogPostUpdate, BlogPostResponse, BlogPostListResponse,
    BlogCategoryResponse, BlogCategoryCreate, BlogCategoryUpdate,
)
from app.schemas.common import Message, PaginatedResponse

logger = logging.getLogger("cremacuadrado.admin")

router = APIRouter()


def _unique_slug(db: Session, base_slug: str, current_id: Optional[int] = None) -> str:
    """Ensure the slug is unique among blog_posts, appending -2, -3, ... if needed."""
    slug = base_slug
    suffix = 2
    while True:
        query = db.query(BlogPost).filter(BlogPost.slug == slug)
        if current_id is not None:
            query = query.filter(BlogPost.id != current_id)
        if not query.first():
            return slug
        slug = f"{base_slug}-{suffix}"
        suffix += 1


def _post_response(post: BlogPost) -> BlogPostResponse:
    return BlogPostResponse(
        id=post.id,
        slug=post.slug,
        title=post.title,
        excerpt=post.excerpt,
        content=post.content,
        author_name=post.author.full_name if post.author else None,
        featured_image_url=post.featured_image_url,
        status=post.status,
        categories=post.categories,
        published_at=post.published_at,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


# =============================================================================
# Blog Categories
# =============================================================================

@router.get("/blog/categories", response_model=List[BlogCategoryResponse])
def list_blog_categories(db: DbSession, admin_user: AdminUser):
    """List all blog categories (recetas, el-obrador, pistacho-en-el-campo, ...)."""
    return db.query(BlogCategory).order_by(BlogCategory.name).all()


@router.post("/blog/categories", response_model=BlogCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_blog_category(data: BlogCategoryCreate, db: DbSession, admin_user: AdminUser):
    """Create a new blog category. Slug is auto-generated from the name if omitted."""
    slug = slugify(data.slug or data.name)
    if db.query(BlogCategory).filter(BlogCategory.slug == slug).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una categoría con este slug")

    category = BlogCategory(slug=slug, name=data.name, description=data.description)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.put("/blog/categories/{category_id}", response_model=BlogCategoryResponse)
def update_blog_category(category_id: int, data: BlogCategoryUpdate, db: DbSession, admin_user: AdminUser):
    """Update a blog category."""
    category = db.query(BlogCategory).filter(BlogCategory.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    if data.name is not None:
        category.name = data.name
    if data.description is not None:
        category.description = data.description
    if data.slug is not None:
        new_slug = slugify(data.slug)
        if db.query(BlogCategory).filter(BlogCategory.slug == new_slug, BlogCategory.id != category_id).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ya existe una categoría con este slug")
        category.slug = new_slug

    db.commit()
    db.refresh(category)
    return category


@router.delete("/blog/categories/{category_id}", response_model=Message)
def delete_blog_category(category_id: int, db: DbSession, admin_user: AdminUser):
    """Delete a blog category. Blocked while it still has posts assigned."""
    category = db.query(BlogCategory).options(joinedload(BlogCategory.posts)).filter(
        BlogCategory.id == category_id
    ).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")

    if category.posts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede eliminar: hay artículos asignados a esta categoría",
        )

    db.delete(category)
    db.commit()
    return Message(message="Categoría eliminada")


# =============================================================================
# Blog Posts
# =============================================================================

@router.get("/blog/posts", response_model=PaginatedResponse[BlogPostListResponse])
def list_blog_posts_admin(
    db: DbSession,
    admin_user: AdminUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    """List all blog posts (drafts included) for the admin CMS."""
    query = db.query(BlogPost).options(
        joinedload(BlogPost.categories),
        joinedload(BlogPost.author),
    )

    if status_filter:
        query = query.filter(BlogPost.status == status_filter)

    if category:
        query = query.join(BlogPost.categories).filter(BlogCategory.slug == category)

    if search:
        query = query.filter(
            BlogPost.title.ilike(f"%{search}%") | BlogPost.slug.ilike(f"%{search}%")
        )

    query = query.order_by(BlogPost.created_at.desc())

    total = query.count()
    offset = (page - 1) * page_size
    posts = query.offset(offset).limit(page_size).all()

    items = [
        BlogPostListResponse(
            id=p.id,
            slug=p.slug,
            title=p.title,
            excerpt=p.excerpt,
            featured_image_url=p.featured_image_url,
            categories=p.categories,
            published_at=p.published_at,
            status=p.status,
        )
        for p in posts
    ]
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/blog/posts/{post_id}", response_model=BlogPostResponse)
def get_blog_post_admin(post_id: int, db: DbSession, admin_user: AdminUser):
    """Get full blog post detail (drafts included)."""
    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories),
        joinedload(BlogPost.author),
    ).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artículo no encontrado")
    return _post_response(post)


@router.post("/blog/posts", response_model=BlogPostResponse, status_code=status.HTTP_201_CREATED)
def create_blog_post(data: BlogPostCreate, db: DbSession, admin_user: AdminUser):
    """Create a new blog post/recipe (draft by default)."""
    slug = _unique_slug(db, slugify(data.slug or data.title))

    if data.status not in ("draft", "published"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido")

    post = BlogPost(
        slug=slug,
        title=data.title,
        excerpt=data.excerpt,
        content=data.content,
        featured_image_url=data.featured_image_url,
        meta_title=data.meta_title,
        meta_description=data.meta_description,
        status=data.status,
        author_id=admin_user.id,
        published_at=datetime.now(timezone.utc) if data.status == "published" else None,
    )

    if data.category_ids:
        post.categories = db.query(BlogCategory).filter(BlogCategory.id.in_(data.category_ids)).all()

    db.add(post)
    db.commit()
    db.refresh(post)

    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories), joinedload(BlogPost.author),
    ).filter(BlogPost.id == post.id).first()
    return _post_response(post)


@router.put("/blog/posts/{post_id}", response_model=BlogPostResponse)
def update_blog_post(post_id: int, data: BlogPostUpdate, db: DbSession, admin_user: AdminUser):
    """Update a blog post/recipe. Publishing sets published_at if not already set."""
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artículo no encontrado")

    if data.title is not None:
        post.title = data.title
    if data.slug is not None and slugify(data.slug) != post.slug:
        post.slug = _unique_slug(db, slugify(data.slug), current_id=post_id)
    if data.excerpt is not None:
        post.excerpt = data.excerpt
    if data.content is not None:
        post.content = data.content
    if data.featured_image_url is not None:
        post.featured_image_url = data.featured_image_url
    if data.meta_title is not None:
        post.meta_title = data.meta_title
    if data.meta_description is not None:
        post.meta_description = data.meta_description

    if data.status is not None:
        if data.status not in ("draft", "published"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Estado no válido")
        if data.status == "published" and post.published_at is None:
            post.published_at = datetime.now(timezone.utc)
        post.status = data.status

    if data.category_ids is not None:
        post.categories = db.query(BlogCategory).filter(BlogCategory.id.in_(data.category_ids)).all()

    db.commit()
    db.refresh(post)

    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories), joinedload(BlogPost.author),
    ).filter(BlogPost.id == post.id).first()
    return _post_response(post)


@router.patch("/blog/posts/{post_id}/publish", response_model=BlogPostResponse)
def publish_blog_post(post_id: int, db: DbSession, admin_user: AdminUser):
    """Publish a draft — sets status=published and published_at if not already set."""
    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories), joinedload(BlogPost.author),
    ).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artículo no encontrado")

    post.status = "published"
    if post.published_at is None:
        post.published_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(post)
    return _post_response(post)


@router.patch("/blog/posts/{post_id}/unpublish", response_model=BlogPostResponse)
def unpublish_blog_post(post_id: int, db: DbSession, admin_user: AdminUser):
    """Revert a post to draft (keeps it out of the public listing)."""
    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories), joinedload(BlogPost.author),
    ).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artículo no encontrado")

    post.status = "draft"
    db.commit()
    db.refresh(post)
    return _post_response(post)


@router.delete("/blog/posts/{post_id}", response_model=Message)
def delete_blog_post(post_id: int, db: DbSession, admin_user: AdminUser):
    """Permanently delete a blog post/recipe."""
    post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artículo no encontrado")

    db.delete(post)
    db.commit()
    return Message(message="Artículo eliminado")
