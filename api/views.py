from pathlib import Path
import os
from uuid import uuid4

from django.conf import settings
from rest_framework import status, views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Job
from .tasks import process_media
from .utils import save_uploaded_file, guess_kind

from .serializers import (
    UploadCreateSerializer,
    JobSerializer,
    PresignRequestSerializer,
    PresignResponseSerializer,
    JobFromKeyRequestSerializer,
)

from .s3 import (
    create_presigned_put,
    create_presigned_get,
    object_url,
)


class UploadAndCreateJobView(views.APIView):
    """
    Accepts a file upload to the Django server (local dev convenience),
    stores it under MEDIA_ROOT, creates a Job, and enqueues Celery task.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = UploadCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        rel_path = save_uploaded_file(ser.validated_data["file"])
        # For local upload path, we don't set a pipeline here; the worker will default per file type.
        job = Job.objects.create(input_path=rel_path)

        process_media.delay(str(job.id))  # queue background processing
        return Response({"job_id": str(job.id)}, status=status.HTTP_202_ACCEPTED)


class JobDetailView(views.APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)

        data = JobSerializer(job).data

        # Build input_url: if the input exists locally, serve via /media; otherwise show S3/MinIO object URL (dev)
        input_rel = job.input_path
        local_path = (settings.MEDIA_ROOT / input_rel)
        if local_path.exists():
            data["input_url"] = request.build_absolute_uri(f"{settings.MEDIA_URL}{input_rel}")
        else:
            # For production you might prefer a signed GET URL; for dev this is fine.
            data["input_url"] = object_url(input_rel)

        # Build outputs list: prefer S3 keys and signed GET URLs
        outs = []
        for o in (job.outputs or []):
            if "s3_key" in o:
                key = o["s3_key"]
                outs.append({
                    "type": o.get("type"),
                    "s3_key": key,
                    "url": create_presigned_get(key),  # time-limited download URL
                })
            else:
                # Fallback for older jobs that stored local paths
                rel = o.get("path")
                outs.append({
                    "type": o.get("type"),
                    "path": rel,
                    "url": request.build_absolute_uri(f"{settings.MEDIA_URL}{rel}"),
                })
        data["outputs"] = outs

        return Response(data)


class PresignUploadView(views.APIView):
    """
    Returns a presigned PUT URL + recommended key so the client can upload
    directly to MinIO/S3 without streaming through Django.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = PresignRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        filename = ser.validated_data["filename"]
        content_type = ser.validated_data.get("content_type") or None

        # Recommend a namespaced key: uploads/<uuid>_<filename>
        safe_name = f"{uuid4().hex}_{os.path.basename(filename)}"
        key = f"uploads/{safe_name}"

        signed = create_presigned_put(key, content_type=content_type)
        resp = {"key": key, "url": signed["url"], "headers": signed.get("headers", {})}
        out = PresignResponseSerializer(resp).data
        return Response(out, status=201)


class CreateJobFromKeyView(views.APIView):
    """
    Creates a Job from an object already uploaded to MinIO/S3 by key.
    Accepts an optional 'pipeline' (e.g., ["thumbnail","watermark","transcode_720p"]).
    If not provided, defaults per file type:
      - image -> ["thumbnail"]
      - video -> ["transcode_720p"]
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = JobFromKeyRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        key = ser.validated_data["key"]
        pipeline = ser.validated_data.get("pipeline") or []

        # Default the pipeline if none provided
        kind = guess_kind(key)  # 'image' | 'video' | 'other'
        if not pipeline:
            if kind == "image":
                pipeline = ["thumbnail"]
            elif kind == "video":
                pipeline = ["transcode_720p"]
            else:
                return Response({"detail": "Unsupported file type. Provide a valid pipeline."}, status=400)

        # Store the key in input_path (same field used for local rel paths)
        job = Job.objects.create(input_path=key, pipeline=pipeline)

        process_media.delay(str(job.id))
        return Response({"job_id": str(job.id)}, status=202)
