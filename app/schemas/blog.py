"""Blog-related schemas."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class BlogCategoryResponse(BaseModel):
    """Blog category response schema."""
    id: int
    slug: str
    name: str
    description: Optional[str]
    
    model_config = {"from_attributes": True}


class BlogPostBase(BaseModel):
    """Base blog post schema."""
    title: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    excerpt: Optional[str] = None
    content: str
    featured_image_url: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None


class BlogPostCreate(BlogPostBase):
    """Blog post creation schema."""
    status: str = "draft"
    category_ids: List[int] = []


class BlogPostUpdate(BaseModel):
    """Blog post update schema."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    slug: Optional[str] = Field(None, min_length=1, max_length=200)
    excerpt: Optional[str] = None
    content: Optional[str] = None
    featured_image_url: Optional[str] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    status: Optional[str] = None
    category_ids: Optional[List[int]] = None


class BlogPostResponse(BaseModel):
    """Blog post response schema."""
    id: int
    slug: str
    title: str
    excerpt: Optional[str]
    content: str
    author_name: Optional[str]
    featured_image_url: Optional[str]
    status: str
    categories: List[BlogCategoryResponse]
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}


class BlogPostListResponse(BaseModel):
    """Blog post list item schema."""
    id: int
    slug: str
    title: str
    excerpt: Optional[str]
    featured_image_url: Optional[str]
    categories: List[BlogCategoryResponse]
    published_at: Optional[datetime]
    
    model_config = {"from_attributes": True}
