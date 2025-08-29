import os, mimetypes
from uuid import uuid4
from django.conf import settings

def save_uploaded_file(djangofile) -> str:
    """Save to MEDIA_ROOT/uploads/<uuid>_<name> and return relative path."""
    uploads_dir = settings.MEDIA_ROOT / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid4().hex}_{os.path.basename(djangofile.name)}"
    dest = uploads_dir / safe_name
    with open(dest, "wb") as f:
        for chunk in djangofile.chunks():
            f.write(chunk)
    # return path relative to MEDIA_ROOT
    return str(dest.relative_to(settings.MEDIA_ROOT))

def guess_kind(path: str) -> str:
    """Return 'image' | 'video' | 'other' based on mimetype/extension."""
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        return "other"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "other"
