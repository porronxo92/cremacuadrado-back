"""
Re-upload all files from backend/static/images/ to Vercel Blob as public,
then update product_images.url and blog_posts.featured_image_url in the DB.

Usage:
    cd backend
    python reupload_as_public.py [--dry-run]
"""
import argparse
import asyncio
import mimetypes
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings

BLOB_API = "https://blob.vercel-storage.com"
API_VERSION = "7"
STATIC_IMAGES = Path(__file__).parent / "static" / "images"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB safety cap


async def upload_public(client: httpx.AsyncClient, content: bytes, pathname: str, mime: str) -> str:
    response = await client.put(
        f"{BLOB_API}/{pathname}",
        content=content,
        headers={
            "Authorization": f"Bearer {settings.BLOB_PUBLIC_READ_WRITE_TOKEN}",
            "x-api-version": API_VERSION,
            "Content-Type": mime,
            "x-add-random-suffix": "0",
            "x-cache-control-max-age": str(60 * 60 * 24 * 30),
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["url"]


async def reupload(dry_run: bool) -> None:
    if not settings.BLOB_PUBLIC_READ_WRITE_TOKEN:
        print("ERROR: BLOB_PUBLIC_READ_WRITE_TOKEN is not set in .env")
        sys.exit(1)

    files = [p for p in STATIC_IMAGES.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    print(f"Found {len(files)} files to re-upload as public\n")

    # Map: old local URL -> new public blob URL
    url_map: dict[str, str] = {}

    if not dry_run:
        async with httpx.AsyncClient() as client:
            for i, local_path in enumerate(files, 1):
                rel = local_path.relative_to(Path(__file__).parent / "static")
                pathname = rel.as_posix()  # e.g. images/products/Crema Pistacho Pura/Pura200gr.jpg
                mime, _ = mimetypes.guess_type(str(local_path))
                mime = mime or "application/octet-stream"

                content = local_path.read_bytes()
                if len(content) > MAX_BYTES:
                    print(f"[{i}/{len(files)}] SKIP  {pathname} (exceeds 10 MB)")
                    continue

                try:
                    blob_url = await upload_public(client, content, pathname, mime)
                    url_map[f"/static/{pathname}"] = blob_url
                    # Also map absolute URL variants
                    for prefix in ["http://localhost:8000", "https://cremacuadrado-back.vercel.app"]:
                        url_map[f"{prefix}/static/{pathname}"] = blob_url
                    print(f"[{i}/{len(files)}] OK  {pathname}")
                    print(f"         -> {blob_url}")
                except Exception as exc:
                    print(f"[{i}/{len(files)}] ERR {pathname}: {exc}")
    else:
        for local_path in files:
            rel = local_path.relative_to(Path(__file__).parent / "static")
            print(f"  [dry-run] {rel.as_posix()}")

    if dry_run:
        print(f"\n[dry-run] Would re-upload {len(files)} files as public.")
        return

    print(f"\nUploaded {len(url_map) // 3} files successfully.")

    if url_map:
        _update_database(url_map)


def _update_database(url_map: dict[str, str]) -> None:
    import app.models.user      # noqa: F401
    import app.models.product   # noqa: F401
    import app.models.order     # noqa: F401
    import app.models.cart      # noqa: F401
    import app.models.payment   # noqa: F401
    import app.models.blog      # noqa: F401
    from app.models.database import SessionLocal
    from app.models.product import ProductImage
    from app.models.blog import BlogPost

    db = SessionLocal()
    try:
        images = db.query(ProductImage).all()
        img_updated = 0
        for img in images:
            new_url = url_map.get(img.url)
            if new_url:
                print(f"  [DB] product_image id={img.id}: {img.url}")
                print(f"         -> {new_url}")
                img.url = new_url
                img_updated += 1

        posts = db.query(BlogPost).all()
        post_updated = 0
        for post in posts:
            if post.featured_image_url:
                new_url = url_map.get(post.featured_image_url)
                if new_url:
                    print(f"  [DB] blog_post id={post.id}: {post.featured_image_url}")
                    print(f"         -> {new_url}")
                    post.featured_image_url = new_url
                    post_updated += 1

        db.commit()
        print(f"\nDB updated: {img_updated} product_images, {post_updated} blog_posts.")
    except Exception as exc:
        db.rollback()
        print(f"DB update ERROR: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-upload static images to Vercel Blob as public")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(reupload(dry_run=args.dry_run))
