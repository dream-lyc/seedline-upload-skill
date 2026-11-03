from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

MediaPurpose = Literal[
    "toefl-stimulus-image",
    "toefl-stimulus-audio",
    "toefl-answer-audio",
    "teacher-avatar",
    "course-cover",
    "platform-payment-qr",
    "ielts-material-image",
    "ielts-material-audio",
    "ielts-sample-audio",
    "ielts-answer-audio",
    "ielts-explanation-video",
]
MediaRole = Literal["teacher", "admin", "student"]


@dataclass(frozen=True, slots=True)
class MediaPurposePolicy:
    allowed_role: MediaRole
    allowed_content_types: frozenset[str]
    max_bytes: int
    min_width: int | None
    min_height: int | None
    crop_ratio: Literal["1:1", "16:9"] | None
    output_format: str
    max_width: int | None
    max_height: int | None
    variants: Mapping[str, str]
    private: bool = False
    max_duration_ms: int | None = None
    hourly_limit: int | None = None


_RASTER_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_AUDIO_TYPES = frozenset(
    {"audio/mpeg", "audio/mp4", "audio/aac", "audio/wav", "audio/ogg", "audio/webm"}
)

MEDIA_PURPOSES: Mapping[MediaPurpose, MediaPurposePolicy] = MappingProxyType(
    {
        "toefl-stimulus-image": MediaPurposePolicy(
            "admin",
            _RASTER_TYPES,
            20 * 1024**2,
            None,
            None,
            None,
            "png",
            None,
            None,
            MappingProxyType({}),
            True,
            None,
            100,
        ),
        "toefl-stimulus-audio": MediaPurposePolicy(
            "admin",
            _AUDIO_TYPES,
            100 * 1024**2,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            7_200_000,
            100,
        ),
        "toefl-answer-audio": MediaPurposePolicy(
            "student",
            _AUDIO_TYPES - {"audio/mpeg", "audio/aac"},
            20 * 1024**2,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            600_000,
            60,
        ),
        "teacher-avatar": MediaPurposePolicy(
            allowed_role="teacher",
            allowed_content_types=_RASTER_TYPES,
            max_bytes=5 * 1024**2,
            min_width=None,
            min_height=None,
            crop_ratio="1:1",
            output_format="webp",
            max_width=1600,
            max_height=1600,
            variants=MappingProxyType({"small": "96x96", "medium": "320x320"}),
        ),
        "course-cover": MediaPurposePolicy(
            allowed_role="teacher",
            allowed_content_types=_RASTER_TYPES,
            max_bytes=10 * 1024**2,
            min_width=640,
            min_height=360,
            crop_ratio="16:9",
            output_format="webp",
            max_width=2560,
            max_height=1440,
            variants=MappingProxyType({"card": "640x360", "detail": "1280x720"}),
        ),
        "platform-payment-qr": MediaPurposePolicy(
            allowed_role="admin",
            allowed_content_types=_RASTER_TYPES,
            max_bytes=5 * 1024**2,
            min_width=300,
            min_height=300,
            crop_ratio=None,
            output_format="png",
            max_width=None,
            max_height=None,
            variants=MappingProxyType({}),
        ),
        "ielts-material-image": MediaPurposePolicy(
            "admin",
            _RASTER_TYPES,
            20 * 1024**2,
            None,
            None,
            None,
            "png",
            None,
            None,
            MappingProxyType({}),
            True,
            None,
            100,
        ),
        "ielts-material-audio": MediaPurposePolicy(
            "admin",
            _AUDIO_TYPES,
            100 * 1024**2,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            7_200_000,
            30,
        ),
        "ielts-sample-audio": MediaPurposePolicy(
            "admin",
            _AUDIO_TYPES,
            100 * 1024**2,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            7_200_000,
            30,
        ),
        "ielts-answer-audio": MediaPurposePolicy(
            "student",
            _AUDIO_TYPES - {"audio/mpeg", "audio/aac"},
            20 * 1024**2,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            600_000,
            60,
        ),
        "ielts-explanation-video": MediaPurposePolicy(
            "admin",
            frozenset({"video/mp4", "video/webm"}),
            1024**3,
            None,
            None,
            None,
            "source",
            None,
            None,
            MappingProxyType({}),
            True,
            14_400_000,
            10,
        ),
    }
)


def object_keys_for(
    *,
    purpose: MediaPurpose,
    media_id: str,
    year: int,
    month: int,
    source_extension: str,
) -> tuple[str, str]:
    """Return private source and immutable, purpose-controlled Master keys."""

    base = f"{purpose}/{year:04d}/{month:02d}/{media_id}"
    incoming = f"incoming/{base}/source.{source_extension}"
    output = MEDIA_PURPOSES[purpose].output_format
    master = f"media/{base}/master.{source_extension if output == 'source' else output}"
    return incoming, master
