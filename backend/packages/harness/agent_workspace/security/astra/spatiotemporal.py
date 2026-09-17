"""Spatio-Temporal Memory and Continuous Perception Cache.

Inspired by Google DeepMind Project Astra's core multimodal architecture:
- Continuous video/screen keyframe timeline ingestion
- Spatial object grounding with 2D/3D bounding boxes
- Retroactive spatial object querying ("Where was object X seen?")
- Temporal window filtering and ambient noise rejection
"""

from __future__ import annotations

import bisect
import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BoundingBox:
    """Normalized spatial coordinates (0.0 to 1.0) for visual grounding."""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    coordinate_type: str = "normalized"  # 'normalized' or 'pixel'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpatialObject:
    """A detected or recognized object in a visual frame."""
    object_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    label: str = ""
    confidence: float = 1.0
    bbox: BoundingBox = field(default_factory=BoundingBox)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bbox"] = self.bbox.to_dict()
        return data


@dataclass
class VideoKeyframe:
    """A temporal keyframe snapshot representing continuous stream perception."""
    frame_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    image_hash: str = ""
    objects: list[SpatialObject] = field(default_factory=list)
    screen_context: str = ""
    ambient_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "image_hash": self.image_hash,
            "objects": [obj.to_dict() for obj in self.objects],
            "screen_context": self.screen_context,
            "ambient_text": self.ambient_text,
            "metadata": self.metadata,
        }


class SpatioTemporalCache:
    """Continuous multimodal timeline and spatial memory cache."""

    def __init__(self, max_frames: int = 1000):
        self.max_frames: int = max_frames
        self._frames: list[VideoKeyframe] = []
        self._timestamps: list[float] = []
        # Inverted index: object_label.lower() -> list of (frame_index, object_idx)
        self._label_index: dict[str, list[tuple[int, int]]] = {}

    def ingest_frame(
        self,
        objects: list[SpatialObject] | None = None,
        screen_context: str = "",
        ambient_text: str = "",
        timestamp: float | None = None,
        image_bytes: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoKeyframe:
        """Ingest a continuous stream keyframe into temporal cache."""
        t = timestamp if timestamp is not None else time.time()
        img_hash = hashlib.sha256(image_bytes).hexdigest() if image_bytes else hashlib.sha256(str(t).encode()).hexdigest()

        frame = VideoKeyframe(
            timestamp=t,
            image_hash=img_hash,
            objects=objects or [],
            screen_context=screen_context,
            ambient_text=ambient_text,
            metadata=metadata or {},
        )

        # Append to sorted timeline
        idx = bisect.bisect_right(self._timestamps, t)
        self._timestamps.insert(idx, t)
        self._frames.insert(idx, frame)

        # Index spatial objects
        for obj_idx, obj in enumerate(frame.objects):
            lbl = obj.label.strip().lower()
            self._label_index.setdefault(lbl, []).append((idx, obj_idx))

        # Evict old frames if capacity exceeded
        if len(self._frames) > self.max_frames:
            self._rebuild_index_after_eviction()

        return frame

    def _rebuild_index_after_eviction(self) -> None:
        """Trim oldest frames and update inverted index."""
        evict_count = len(self._frames) - self.max_frames
        self._frames = self._frames[evict_count:]
        self._timestamps = self._timestamps[evict_count:]

        self._label_index.clear()
        for f_idx, frame in enumerate(self._frames):
            for obj_idx, obj in enumerate(frame.objects):
                lbl = obj.label.strip().lower()
                self._label_index.setdefault(lbl, []).append((f_idx, obj_idx))

    def find_object_history(self, label: str) -> list[dict[str, Any]]:
        """Retroactively locate an object across time ('Where did I leave my glasses?')."""
        lbl = label.strip().lower()
        matches: list[dict[str, Any]] = []

        for f_idx, obj_idx in self._label_index.get(lbl, []):
            if f_idx < len(self._frames):
                frame = self._frames[f_idx]
                obj = frame.objects[obj_idx]
                matches.append({
                    "frame_id": frame.frame_id,
                    "timestamp": frame.timestamp,
                    "screen_context": frame.screen_context,
                    "ambient_text": frame.ambient_text,
                    "object": obj.to_dict(),
                })

        return matches

    def get_most_recent_location(self, label: str) -> dict[str, Any] | None:
        """Return the last seen timestamp and bounding box of a named object."""
        history = self.find_object_history(label)
        if not history:
            return None
        return history[-1]

    def get_recent_timeline(self, window_seconds: float = 60.0) -> list[dict[str, Any]]:
        """Retrieve keyframes within the last N seconds to construct prompt context."""
        if not self._timestamps:
            return []

        latest_t = self._timestamps[-1]
        cutoff_t = latest_t - window_seconds
        start_idx = bisect.bisect_left(self._timestamps, cutoff_t)

        return [f.to_dict() for f in self._frames[start_idx:]]

    def get_frame_by_id(self, frame_id: str) -> VideoKeyframe | None:
        for f in self._frames:
            if f.frame_id == frame_id:
                return f
        return None

    def total_frames(self) -> int:
        return len(self._frames)
