import uuid
from django.db import models

class Job(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        STARTED = "STARTED"
        SUCCESS = "SUCCESS"
        FAILURE = "FAILURE"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    input_path = models.CharField(max_length=512)     # relative to MEDIA_ROOT or S3 key
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    progress = models.PositiveSmallIntegerField(default=0)  # 0..100
    outputs = models.JSONField(default=list, blank=True)    # [{type, s3_key/path, ...}]
    error = models.TextField(blank=True, default="")
    # NEW: ordered list of steps, e.g. ["thumbnail", "watermark", "transcode_720p"]
    pipeline = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
