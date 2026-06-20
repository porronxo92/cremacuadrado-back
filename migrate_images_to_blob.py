"""
One-time migration script: upload all files in backend/static/ to Vercel Blob
and optionally update product_images.url in the database.

Usage:
    cd backend
    python migrate_images_to_blob.py [--dry-run] [--update-db]

Options:
    --dry-run    List files that would be uploaded without actually uploading.
    --update-db  After uploading, update product_images.url and blog_posts.featured_image
                 in the database so they point to the new Blob URLs.
"""
import argparse
import asyncio
import mimetypes
import os
import sys
from pathlib import Path

import httpx

# Ensure the app package is importable when running from backend/
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings

BLOB_API = "https://blob.vercel-storage.com"
API_VERSION = "7"
STATIC_ROOT = Path(__file__).parent / "static"


async def upload_file(client: httpx.AsyncClient, local_path: Path, pathname: str) -> str:
    content = local_path.read_bytes()
    mime, _ = mimetypes.guess_type(str(local_path))
    response = await client.put(
        f"{BLOB_API}/{pathname}",
        content=content,
        headers={
            "Authorization": f"Bearer {settings.BLOB_READ_WRITE_TOKEN}",
            "x-api-version": API_VERSION,
            "Content-Type": mime or "application/octet-stream",
            "x-add-random-suffix": "0",
            "x-cache-control-max-age": str(60 * 60 * 24 * 30),
        },
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()["url"]


async def migrate(dry_run: bool, update_db: bool) -> None:
    if not settings.BLOB_READ_WRITE_TOKEN:
        print("ERROR: BLOB_READ_WRITE_TOKEN is not set in .env")
        sys.exit(1)

    files = [p for p in STATIC_ROOT.rglob("*") if p.is_file()]
    print(f"Found {len(files)} files under {STATIC_ROOT}")

    if dry_run:
        for f in files:
            rel = f.relative_to(STATIC_ROOT)
            print(f"  [dry-run] would upload: {rel}")
        return

    # Map old relative URL → new blob URL for DB update
    url_map: dict[str, str] = {}

    async with httpx.AsyncClient() as client:
        for i, local_path in enumerate(files, 1):
            rel = local_path.relative_to(STATIC_ROOT)
            # Use forward slashes for blob pathname
            pathname = rel.as_posix()
            old_url_relative = f"/static/{pathname}"

            try:
                blob_url = await upload_file(client, local_path, pathname)
                url_map[old_url_relative] = blob_url
                print(f"[{i}/{len(files)}] OK  {pathname}")
            except Exception as exc:
                print(f"[{i}/{len(files)}] ERR {pathname}: {exc}")

    print(f"\nUploaded {len(url_map)}/{len(files)} files.")

    if update_db and url_map:
        _update_database(url_map)


def _update_database(url_map: dict[str, str]) -> None:
    from app.models.database import SessionLocal
    from app.models.product import ProductImage
    from app.models.blog import BlogPost

    db = SessionLocal()
    try:
        images = db.query(ProductImage).all()
        updated = 0
        for img in images:
            new_url = url_map.get(img.url)
            if new_url:
                img.url = new_url
                updated += 1

        posts = db.query(BlogPost).all()
        for post in posts:
            if post.featured_image:
                new_url = url_map.get(post.featured_image)
                if new_url:
                    post.featured_image = new_url
                    updated += 1

        db.commit()
        print(f"Updated {updated} database records.")
    except Exception as exc:
        db.rollback()
        print(f"DB update failed: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate static images to Vercel Blob")
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    parser.add_argument("--update-db", action="store_true", help="Update DB URLs after upload")
    args = parser.parse_args()

    asyncio.run(migrate(dry_run=args.dry_run, update_db=args.update_db))
