"""Local file facts and disk-spooled complete multipart bodies."""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from question_content_contracts.media import MEDIA_PURPOSES
from question_content_contracts.media_context import validate_context_shape

INTEGRATION_PURPOSES = {
    "ielts-material-image",
    "ielts-material-audio",
    "ielts-sample-audio",
    "ielts-explanation-video",
    "toefl-stimulus-image",
    "toefl-stimulus-audio",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while value := stream.read(1024 * 1024):
            digest.update(value)
    return digest.hexdigest()


def probe_file(path, purpose, content_type):
    try:
        return _probe_file(path, purpose, content_type)
    except (wave.Error, EOFError, subprocess.TimeoutExpired) as error:
        raise ValueError("图片或音视频无法安全解码/探测，请修正源文件后继续") from error


def _probe_file(path, purpose, content_type):
    if purpose not in INTEGRATION_PURPOSES:
        raise ValueError("此媒体用途不允许集成上传")
    policy = MEDIA_PURPOSES[purpose]
    mime = content_type.split(";", 1)[0].strip().lower()
    path = Path(path)
    if mime not in policy.allowed_content_types or path.stat().st_size > policy.max_bytes:
        raise ValueError("媒体MIME或字节数不符合用途策略")
    if not path.stat().st_size:
        raise ValueError("媒体文件不能为空")
    result = {
        "purpose": purpose,
        "state": "confirmed",
        "content_type": mime,
        "byte_size": path.stat().st_size,
        "duration_ms": None,
        "width": None,
        "height": None,
    }
    if mime.startswith("image/"):
        from PIL import Image

        try:
            with Image.open(path) as image:
                actual = Image.MIME.get(image.format or "")
                image.verify()
            with Image.open(path) as image:
                result.update(width=image.width, height=image.height)
        except Image.DecompressionBombError as error:
            raise ValueError("图片解码尺寸超过安全限制，请修正源文件") from error
        if actual != mime:
            raise ValueError("实际图片类型与声明MIME不一致")
    elif mime == "audio/wav":
        with wave.open(str(path), "rb") as audio:
            result["duration_ms"] = round(audio.getnframes() * 1000 / audio.getframerate())
    else:
        binary = shutil.which("ffprobe")
        if not binary:
            raise ValueError("需要ffprobe核验真实音视频时长；请安装ffmpeg后继续，不能估算时间轴")
        output = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if output.returncode:
            raise ValueError("音视频文件无法解码或探测")
        info = json.loads(output.stdout)
        streams = info.get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if mime.startswith("audio/") and (not audio or video):
            raise ValueError("音频须有音轨且不能包含视频轨")
        if mime.startswith("video/") and (not video or not audio):
            raise ValueError("解析视频须有视频轨和音轨")
        formats = set(info.get("format", {}).get("format_name", "").split(","))
        expected = {
            "audio/mpeg": {"mp3"},
            "audio/aac": {"aac"},
            "audio/mp4": {"mov", "mp4", "m4a"},
            "audio/ogg": {"ogg"},
            "audio/webm": {"matroska", "webm"},
            "video/mp4": {"mov", "mp4"},
            "video/webm": {"matroska", "webm"},
        }
        if not formats & expected.get(mime, set()):
            raise ValueError("实际音视频容器与声明MIME不一致")
        result["duration_ms"] = round(float(info["format"]["duration"]) * 1000)
        if video:
            result.update(width=video[0].get("width"), height=video[0].get("height"))
    if policy.max_duration_ms is not None and (
        not result["duration_ms"] or result["duration_ms"] > policy.max_duration_ms
    ):
        raise ValueError("音视频时长无效或超过用途限制")
    return result


def snapshot_media(batch, entry):
    from .ledger import uuid7

    purpose = entry["purpose"]
    mime = entry["content_type"].split(";", 1)[0].strip().lower()
    context = validate_context_shape(purpose, entry.get("context", {}))
    if purpose not in INTEGRATION_PURPOSES:
        raise ValueError("不支持此上传用途")
    if purpose == "ielts-explanation-video" and not entry.get("after_content", False):
        raise ValueError("解析视频必须在内容创建后上传")
    reference = entry.get("ref") or uuid7()
    from uuid import UUID

    if str(UUID(reference)) != reference:
        raise ValueError("媒体ref须为规范UUID，占位身份将在上传后回填")
    source = Path(entry["path"]).expanduser().resolve(strict=True)
    policy = MEDIA_PURPOSES[purpose]
    if (
        mime not in policy.allowed_content_types
        or not 0 < source.stat().st_size <= policy.max_bytes
    ):
        raise ValueError("媒体MIME或字节数不符合用途策略，未复制源文件")
    target = batch.root / "sources" / (reference + "-" + uuid7())
    target.parent.mkdir(mode=0o700, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        copied = 0
        while chunk := src.read(min(1024 * 1024, policy.max_bytes - copied + 1)):
            copied += len(chunk)
            if copied > policy.max_bytes:
                raise ValueError("媒体在读取过程中变大并超过用途限制")
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    os.chmod(target, 0o600)
    metadata = probe_file(target, purpose, mime)
    metadata["local_reference"] = True
    return {
        "ref": reference,
        "source_file": str(target.relative_to(batch.root)),
        "file_sha256": sha256_file(target),
        "purpose": purpose,
        "content_type": mime,
        "context": context,
        "after_content": purpose == "ielts-explanation-video",
        "metadata": metadata,
    }


def multipart_file(batch, entry):
    source = (batch.root / entry["source_file"]).resolve()
    if (
        not source.is_relative_to(batch.root.resolve())
        or sha256_file(source) != entry["file_sha256"]
    ):
        raise ValueError("原上传源文件已修改，不能复用原幂等键")
    boundary = "acebro-" + secrets.token_hex(24)
    fd, name = tempfile.mkstemp(prefix=".multipart-", dir=batch.root)
    try:
        with os.fdopen(fd, "wb") as dst:
            for key, value in [
                ("purpose", entry["purpose"]),
                ("file_sha256", entry["file_sha256"]),
                (
                    "context",
                    json.dumps(
                        entry["context"],
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ]:
                dst.write(
                    (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n'
                        f"\r\n{value}\r\n"
                    ).encode()
                )
            dst.write(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="source"\r\nContent-Type: {entry["content_type"]}\r\n\r\n'
                ).encode()
            )
            with source.open("rb") as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.write(f"\r\n--{boundary}--\r\n".encode())
        return Path(name), "multipart/form-data; boundary=" + boundary
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
