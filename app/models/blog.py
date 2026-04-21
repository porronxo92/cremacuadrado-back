"""
Blog models - BlogPosts, BlogCategories.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship

from app.models.database import Base


# Many-to-many association table for blog posts and categories
blog_post_categories = Table(
    'blog_post_categories',
    Base.metadata,
    Column('post_id', Integer, ForeignKey('blog_posts.id', ondelete='CASCADE'), primary_key=True),
    Column('category_id', Integer, ForeignKey('blog_categories.id', ondelete='CASCADE'), primary_key=True),
)


class BlogCategory(Base):
    """Blog category model."""
    __tablename__ = "blog_categories"
    
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    posts = relationship("BlogPost", secondary=blog_post_categories, back_populates="categories")
    
    def __repr__(self):
        return f"<BlogCategory {self.name}>"


class BlogPost(Base):
    """Blog post model."""
    __tablename__ = "blog_posts"
    
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(200), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    excerpt = Column(Text, nullable=True)  # Short summary
    content = Column(Text, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    featured_image_url = Column(String(500), nullable=True)
    status = Column(String(20), default="draft", nullable=False)  # draft, published
    published_at = Column(DateTime, nullable=True)
    
    # SEO
    meta_title = Column(String(200), nullable=True)
    meta_description = Column(String(500), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    author = relationship("User")
    categories = relationship("BlogCategory", secondary=blog_post_categories, back_populates="posts")
    
    @property
    def is_published(self) -> bool:
        """Check if post is published."""
        return self.status == "published" and self.published_at is not None
    
    def __repr__(self):
        return f"<BlogPost {self.title}>"
