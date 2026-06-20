"""
Update database image URLs to point to Vercel Blob.

Reads all blobs under the 'images/' prefix from the Vercel Blob API,
then updates product_images.url and blog_posts.featured_image_url
where the current value matches the old local path.

Usage:
    cd backend
    python update_db_blob_urls.py [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings

BLOB_API = "https://blob.vercel-storage.com"
API_VERSION = "7"


async def list_all_blobs() -> list[dict]:
    """Paginate through all blobs and return them."""
    blobs = []
    cursor = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {"prefix": "images/", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor

            response = await client.get(
                BLOB_API,
                params=params,
                headers={
                    "Authorization": f"Bearer {settings.BLOB_READ_WRITE_TOKEN}",
                    "x-api-version": API_VERSION,
                },
            )
            response.raise_for_status()
            data = response.json()
            blobs.extend(data.get("blobs", []))

            if not data.get("hasMore"):
                break
            cursor = data.get("cursor")

    return blobs


def build_url_map(blobs: list[dict]) -> dict[str, str]:
    """
    Build mapping from old local URL patterns -> new blob URL.

    Old patterns in the DB can be:
      /static/images/products/...
      http://localhost:8000/static/images/products/...
      https://cremacuadrado-back.vercel.app/static/images/products/...
    """
    url_map: dict[str, str] = {}
    for blob in blobs:
        pathname: str = blob["pathname"]   # e.g. images/products/Crema Pistacho Pura/...
        blob_url: str = blob["url"]

        # Match /static/{pathname}
        url_map[f"/static/{pathname}"] = blob_url

        # Match with any known BASE_URL prefix
        for prefix in [
            "http://localhost:8000",
            "https://cremacuadrado-back.vercel.app",
        ]:
            url_map[f"{prefix}/static/{pathname}"] = blob_url

    return url_map


def update_database(url_map: dict[str, str], dry_run: bool) -> None:
    from app.models.database import SessionLocal
    # Import all models so SQLAlchemy resolves all relationships before querying
    import app.models.user        # noqa: F401
    import app.models.product     # noqa: F401
    import app.models.order       # noqa: F401
    import app.models.cart        # noqa: F401
    import app.models.payment     # noqa: F401
    import app.models.blog        # noqa: F401
    from app.models.product import ProductImage
    from app.models.blog import BlogPost

    db = SessionLocal()
    try:
        # --- ProductImage ---
        images = db.query(ProductImage).all()
        img_updates = 0
        for img in images:
            new_url = url_map.get(img.url)
            if new_url:
                if dry_run:
                    print(f"  [product_image id={img.id}] {img.url}\n    -> {new_url}")
                else:
                    img.url = new_url
                img_updates += 1

        # --- BlogPost.featured_image_url ---
        posts = db.query(BlogPost).all()
        post_updates = 0
        for post in posts:
            if post.featured_image_url:
                new_url = url_map.get(post.featured_image_url)
                if new_url:
                    if dry_run:
                        print(f"  [blog_post id={post.id}] {post.featured_image_url}\n    -> {new_url}")
                    else:
                        post.featured_image_url = new_url
                    post_updates += 1

        total = img_updates + post_updates
        if dry_run:
            print(f"\n[dry-run] Would update {img_updates} product_images and {post_updates} blog_posts ({total} total).")
        else:
            db.commit()
            print(f"Updated {img_updates} product_images and {post_updates} blog_posts ({total} total).")

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        raise
    finally:
        db.close()


async def main(dry_run: bool) -> None:
    if not settings.BLOB_READ_WRITE_TOKEN:
        print("ERROR: BLOB_READ_WRITE_TOKEN is not set in .env")
        sys.exit(1)

    print("Listing blobs from Vercel Blob...")
    blobs = await list_all_blobs()
    print(f"Found {len(blobs)} blobs under images/")

    url_map = build_url_map(blobs)
    print(f"Built {len(url_map)} URL mappings\n")

    if dry_run:
        print("--- DRY RUN — no changes will be written ---\n")

    update_database(url_map, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update DB URLs to Vercel Blob URLs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
