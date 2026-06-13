"""Fix duplicated paths in URLs."""
from app.models.product import ProductImage
from app.models.blog import BlogPost
from app.models.database import engine
from sqlalchemy.orm import Session

def clean_urls():
    with Session(engine) as db:
        # Fix product images with duplicated /images/images
        images = db.query(ProductImage).all()
        for img in images:
            if "/images/images/" in img.url:
                img.url = img.url.replace("/images/images/", "/images/")
                print(f"Updated image {img.id}: {img.url}")

        # Fix blog posts
        posts = db.query(BlogPost).all()
        for post in posts:
            if post.featured_image_url and "/images/images/" in post.featured_image_url:
                post.featured_image_url = post.featured_image_url.replace("/images/images/", "/images/")
                print(f"Updated blog post {post.id}: {post.featured_image_url}")

        db.commit()
        print("Done: URLs fixed")

if __name__ == "__main__":
    clean_urls()
