import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "media_pipeline.settings")

celery_app = Celery("media_pipeline")
celery_app.config_from_object("django.conf:settings", namespace="CELERY")
celery_app.autodiscover_tasks()
