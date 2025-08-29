import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from celery import shared_task
from django.conf import settings

from .models import Job
from .utils import guess_kind
from .s3 import get_s3_client, upload_file, upload_dir


def _update(job: Job, *, status=None, progress=None, error=None, outputs_append=None):
    if status:
        job.status = status
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
    if error is not None:
        job.error = error
    if outputs_append:
        outs = job.outputs or []
        outs.extend(outputs_append)
        job.outputs = outs
    job.save(update_fields=["status", "progress", "error", "outputs", "updated_at"])


def _maybe_download_from_s3(rel_or_key: str) -> tuple[Path, bool]:
    """
    Return (local_path, is_temp). If the file exists under MEDIA_ROOT, use it.
    Otherwise treat rel_or_key as an S3 key, download to a temp file, and return that.
    """
    local_candidate = Path(settings.MEDIA_ROOT) / rel_or_key
    if local_candidate.exists():
        return local_candidate, False

    s3 = get_s3_client()
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=Path(rel_or_key).suffix)
    s3.download_file(settings.S3_BUCKET, rel_or_key, tf.name)
    tf.close()
    return Path(tf.name), True


def _progress_for_step(idx: int, total: int) -> int:
    """Map step index to a 10..95 range; leave last 5% for finalize."""
    if total <= 0:
        return 100
    start, end = 10.0, 95.0
    return int(start + (end - start) * (idx / total))


def _step_thumbnail_image(input_abs: Path, base_stem: str) -> dict:
    """Create 512px JPEG thumbnail, upload to S3/MinIO, return output descriptor."""
    img = Image.open(input_abs).convert("RGB")
    img.thumbnail((512, 512))

    out_rel = f"outputs/{base_stem}_thumb.jpg"
    out_abs = Path(settings.MEDIA_ROOT) / out_rel
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_abs, format="JPEG", quality=90)

    upload_file(str(out_abs), out_rel, content_type="image/jpeg")
    # Optionally: out_abs.unlink(missing_ok=True)
    return {"type": "thumbnail", "s3_key": out_rel}


def _step_watermark_image(input_abs: Path, base_stem: str) -> dict:
    """Add a subtle text watermark bottom-right, upload to S3/MinIO."""
    img = Image.open(input_abs).convert("RGBA")
    w, h = img.size

    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    text = "WATERMARK"

    try:
        font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = max(8, int(min(w, h) * 0.02))
    pos = (w - text_w - pad, h - text_h - pad)

    draw.text(pos, text, fill=(255, 255, 255, 128), font=font)
    watermarked = Image.alpha_composite(img, overlay).convert("RGB")

    out_rel = f"outputs/{base_stem}_wm.jpg"
    out_abs = Path(settings.MEDIA_ROOT) / out_rel
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    watermarked.save(out_abs, format="JPEG", quality=90)

    upload_file(str(out_abs), out_rel, content_type="image/jpeg")
    return {"type": "watermark", "s3_key": out_rel}


def _step_transcode_720p_video(input_abs: Path, base_stem: str) -> dict:
    """Transcode to H.264/AAC 720p MP4, upload to S3/MinIO."""
    out_rel = f"outputs/{base_stem}_720p.mp4"
    out_abs = Path(settings.MEDIA_ROOT) / out_rel
    out_abs.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_abs),
        "-vf", "scale=-2:720",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_abs),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    upload_file(str(out_abs), out_rel, content_type="video/mp4")
    return {"type": "video_720p", "s3_key": out_rel}


def _step_hls_720p_video(input_abs: Path, base_stem: str) -> dict:
    """
    Generate HLS (m3u8 + .ts segments) at 720p and upload whole folder to S3/MinIO.
    Returns {"type":"hls_720p","s3_key":"outputs/hls/<base_stem>/index.m3u8"}.
    """
    out_dir_rel = f"outputs/hls/{base_stem}"
    out_dir_abs = Path(settings.MEDIA_ROOT) / out_dir_rel
    out_dir_abs.parent.mkdir(parents=True, exist_ok=True)
    out_dir_abs.mkdir(parents=True, exist_ok=True)

    playlist = out_dir_abs / "index.m3u8"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_abs),
        "-vf", "scale=-2:720",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_segment_filename", str(out_dir_abs / "seg_%04d.ts"),
        str(playlist),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Upload playlist + segments
    upload_dir(str(out_dir_abs), out_dir_rel)
    return {"type": "hls_720p", "s3_key": f"{out_dir_rel}/index.m3u8"}


@shared_task(bind=True)
def process_media(self, job_id: str):
    job = Job.objects.get(pk=job_id)
    _update(job, status=Job.Status.STARTED, progress=5)

    # Input can be local (MEDIA_ROOT/...) or an S3 key (uploads/...)
    key_or_rel = job.input_path
    input_abs, is_temp = _maybe_download_from_s3(key_or_rel)
    input_kind = guess_kind(key_or_rel)  # 'image' | 'video' | 'other'
    base_stem = Path(key_or_rel).stem

    # Pipeline comes from the DB (set in CreateJobFromKeyView). If empty, default by type.
    pipeline = job.pipeline or []
    if not pipeline:
        if input_kind == "image":
            pipeline = ["thumbnail"]
        elif input_kind == "video":
            pipeline = ["transcode_720p"]
        else:
            _update(job, status=Job.Status.FAILURE, error="Unsupported file type; no pipeline.", progress=100)
            return

    total = len(pipeline)
    outputs = []

    try:
        for idx, step in enumerate(pipeline, start=1):
            # Progress entering this step
            _update(job, progress=_progress_for_step(idx - 1, total))

            if step == "thumbnail":
                if input_kind != "image":
                    continue
                out = _step_thumbnail_image(input_abs, base_stem)
                outputs.append(out)

            elif step == "watermark":
                if input_kind != "image":
                    continue
                out = _step_watermark_image(input_abs, base_stem)
                outputs.append(out)

            elif step == "transcode_720p":
                if input_kind != "video":
                    continue
                out = _step_transcode_720p_video(input_abs, base_stem)
                outputs.append(out)

            elif step == "hls_720p":
                if input_kind != "video":
                    continue
                out = _step_hls_720p_video(input_abs, base_stem)
                outputs.append(out)

            else:
                raise RuntimeError(f"Unsupported step: {step}")

            # Progress after step completion
            _update(job, progress=_progress_for_step(idx, total))

        _update(job, status=Job.Status.SUCCESS, progress=100, outputs_append=outputs)

    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
        _update(job, status=Job.Status.FAILURE, error=err[:4000], progress=100)
        raise
    except Exception as e:
        _update(job, status=Job.Status.FAILURE, error=str(e)[:4000], progress=100)
        raise
    finally:
        if is_temp:
            try:
                input_abs.unlink(missing_ok=True)
            except Exception:
                pass
