# Cloud-Native Media Pipeline (Django + Celery + FFmpeg + S3/MinIO)

A production-style backend for **media ingestion and processing**. Clients upload directly to object storage via **presigned URLs**; background **Celery** workers run image/video transforms (thumbnail, watermark, MP4/HLS). The API exposes **job status** and **time-limited signed downloads**.

> Built to demonstrate modern cloud patterns (direct-to-object-storage, async workers, idempotent steps, signed URLs) — runs locally **without Docker**.

---

## ✨ Highlights

- Direct-to-S3/MinIO uploads (no large payloads through the API)
- Async pipelines with Celery + Redis and progress tracking
- Multiple outputs per job (JPG/MP4/HLS) with **signed GET** URLs
- PostgreSQL models/migrations (SQLite fallback for quick dev)
- FFmpeg (video) + Pillow (images)
- Clean Django REST Framework API

---

## 🧱 Tech Stack

**API**: Django + DRF  
**Workers/Queue**: Celery + Redis  
**DB**: PostgreSQL (fallback: SQLite)  
**Object Storage**: S3/MinIO (boto3)  
**Processing**: FFmpeg (video), Pillow (images)

---

## 🏗️ Architecture
flowchart LR
    client[Client] -->|POST /api/uploads/presign| api[API (Django/DRF)]
    client -->|PUT file via presigned URL| s3[(S3 / MinIO)]
    api -->|POST /api/jobs/from-key| queue[(Redis)]
    worker[Celery Worker] -->|pull job| queue
    worker -->|FFmpeg / Pillow| s3
    worker -->|status / progress| db[(PostgreSQL)]
    api -->|GET /api/jobs/:id| client

🚀 Local Setup
Install

bash
Copy code
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
Configure

bash
Copy code
cp .env.example .env
# Fill in local values (Postgres/Redis/MinIO). Do not commit .env
Run

bash
Copy code
# DB
python manage.py migrate

# API
python manage.py runserver  # http://127.0.0.1:8000

# Worker (new terminal)
source venv/bin/activate
celery -A media_pipeline.celery_app worker --loglevel=info
Ensure Redis and MinIO/S3 are running and the bucket from .env exists.

🔌 API Reference (sample requests & responses)
1) Request a presigned upload URL
POST /api/uploads/presign/

Request (JSON)

json
Copy code
{
  "filename": "image.png",
  "content_type": "image/png"
}
Response (JSON)

json
Copy code
{
  "key": "uploads/957c949f..._image.png",
  "url": "http://127.0.0.1:9000/media-local/uploads/957c9...png?...signature...",
  "headers": {
    "Content-Type": "image/png"
  }
}
Next step (client-side):

Perform an HTTP PUT to url with the file bytes.

Include the suggested headers if provided.

2) Enqueue processing for an uploaded object
POST /api/jobs/from-key/

Request (JSON)

json
Copy code
{
  "key": "uploads/957c949f..._image.png",
  "pipeline": ["thumbnail", "watermark"]
}
Allowed steps: "thumbnail", "watermark", "transcode_720p", "hls_720p"

Response (JSON)

json
Copy code
{ "job_id": "d7b8f4bf-5809-4c62-97a4-c008686e881d" }
3) (Dev convenience) Upload to Django then process
POST /api/jobs/upload/ (multipart/form-data)

Form fields

file: the media file

Response (JSON)

json
Copy code
{ "job_id": "fe3a1ecb-3c2e-4716-95aa-9f0ff46e2387" }
4) Get job status & outputs
GET /api/jobs/{job_id}/

Response (JSON)

json
Copy code
{
  "id": "d7b8f4bf-5809-4c62-97a4-c008686e881d",
  "status": "SUCCESS",
  "progress": 100,
  "outputs": [
    {
      "type": "thumbnail",
      "s3_key": "outputs/1f7fb8a2..._presign_thumb.jpg",
      "url": "http://127.0.0.1:9000/media-local/outputs/...thumb.jpg?...signature..."
    },
    {
      "type": "watermark",
      "s3_key": "outputs/1f7fb8a2..._presign_wm.jpg",
      "url": "http://127.0.0.1:9000/media-local/outputs/...wm.jpg?...signature..."
    }
  ],
  "error": "",
  "pipeline": ["thumbnail", "watermark"],
  "created_at": "2025-08-29T18:06:18.752043Z",
  "updated_at": "2025-08-29T18:06:19.083079Z",
  "input_url": "http://127.0.0.1:9000/media-local/uploads/1f7fb8a2..._presign.png"
}
Notes

status: PENDING | STARTED | SUCCESS | FAILURE

outputs[*].url are signed GET URLs with short TTL

For HLS, the playlist URL is signed. In production, ensure segments are accessible (e.g., signed segment URLs or a temporary public-read policy just for /outputs/hls/*).

