from rest_framework import serializers
from .models import Job

# Allowed transformation steps for multi-step pipelines
ALLOWED_STEPS = {"thumbnail", "watermark", "transcode_720p", "hls_720p"}


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = [
            "id",
            "status",
            "progress",
            "outputs",
            "error",
            "pipeline",     # expose pipeline on the API
            "created_at",
            "updated_at",
        ]


class UploadCreateSerializer(serializers.Serializer):
    file = serializers.FileField()
    # optional: later we could add a 'pipeline' hint here, but for now keep simple


class PresignRequestSerializer(serializers.Serializer):
    filename = serializers.CharField()
    content_type = serializers.CharField(required=False, allow_blank=True)


class PresignResponseSerializer(serializers.Serializer):
    key = serializers.CharField()
    url = serializers.URLField()
    headers = serializers.DictField(child=serializers.CharField(), required=False)


class JobFromKeyRequestSerializer(serializers.Serializer):
    key = serializers.CharField()
    pipeline = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    def validate_pipeline(self, value):
        """
        Validate that all requested steps are supported.
        De-duplicate while preserving order.
        """
        if not value:
            return value
        bad = [s for s in value if s not in ALLOWED_STEPS]
        if bad:
            raise serializers.ValidationError(
                f"Unsupported steps: {bad}. Allowed: {sorted(ALLOWED_STEPS)}"
            )
        seen = set()
        deduped = []
        for s in value:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped
