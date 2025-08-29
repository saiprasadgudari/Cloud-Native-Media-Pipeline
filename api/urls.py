from django.urls import path
from .views import UploadAndCreateJobView, JobDetailView, PresignUploadView, CreateJobFromKeyView

urlpatterns = [
    path("jobs/upload/", UploadAndCreateJobView.as_view(), name="upload_create_job"),  # old (server upload)
    path("jobs/<uuid:job_id>/", JobDetailView.as_view(), name="job_detail"),
    path("uploads/presign/", PresignUploadView.as_view(), name="uploads_presign"),
    path("jobs/from-key/", CreateJobFromKeyView.as_view(), name="jobs_from_key"),
]
