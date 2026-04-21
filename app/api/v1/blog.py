"""
Blog API endpoints.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession
from app.models.blog import BlogPost, BlogCategory
from app.schemas.blog import (
    BlogPostResponse, BlogPostListResponse, BlogCategoryResponse
)
from app.schemas.common import PaginatedResponse
from app.config import settings

router = APIRouter()


@router.get("/posts", response_model=PaginatedResponse[BlogPostListResponse])
async def list_posts(
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    category: Optional[str] = None,
):
    """List published blog posts."""
    query = db.query(BlogPost).options(
        joinedload(BlogPost.categories),
        joinedload(BlogPost.author),
    ).filter(BlogPost.status == "published")
    
    # Category filter
    if category:
        query = query.join(BlogPost.categories).filter(BlogCategory.slug == category)
    
    # Order by published date
    query = query.order_by(BlogPost.published_at.desc())
    
    # Count total
    total = query.count()
    
    # Paginate
    offset = (page - 1) * page_size
    posts = query.offset(offset).limit(page_size).all()
    
    items = [
        BlogPostListResponse(
            id=post.id,
            slug=post.slug,
            title=post.title,
            excerpt=post.excerpt,
            featured_image_url=post.featured_image_url,
            categories=post.categories,
            published_at=post.published_at,
        )
        for post in posts
    ]
    
    return PaginatedResponse.create(items, total, page, page_size)


@router.get("/posts/{slug}", response_model=BlogPostResponse)
async def get_post(slug: str, db: DbSession):
    """Get blog post by slug."""
    post = db.query(BlogPost).options(
        joinedload(BlogPost.categories),
        joinedload(BlogPost.author),
    ).filter(
        BlogPost.slug == slug,
        BlogPost.status == "published"
    ).first()
    
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artículo no encontrado"
        )
    
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


@router.get("/categories", response_model=List[BlogCategoryResponse])
async def list_categories(db: DbSession):
    """List blog categories."""
    categories = db.query(BlogCategory).order_by(BlogCategory.name).all()
    return categories
