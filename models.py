"""Shared data models."""
from __future__ import annotations

from dataclasses import dataclass, field
import uuid


@dataclass
class Episode:
    title: str
    source_url: str
    timestamp: str       # ISO-8601
    audio_path: str          # relative path inside output_dir
    description: str = ""    # short summary of the article
    thumbnail_url: str = ""  # og:image or twitter:image from source page
    hidden: bool = False     # hidden episodes are excluded from the public UI
    podbean_episode_id: str = ""   # Podbean episode ID (empty = not published)
    podbean_episode_url: str = ""  # Podbean episode permalink
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "source_url": self.source_url,
            "timestamp": self.timestamp,
            "audio_path": self.audio_path,
            "thumbnail_url": self.thumbnail_url,
            "hidden": self.hidden,
            "podbean_episode_id": self.podbean_episode_id,
            "podbean_episode_url": self.podbean_episode_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Episode":
        return cls(
            id=data["id"],
            title=data["title"],
            description=data.get("description", ""),
            source_url=data["source_url"],
            timestamp=data["timestamp"],
            audio_path=data["audio_path"],
            thumbnail_url=data.get("thumbnail_url", ""),
            hidden=data.get("hidden", False),
            podbean_episode_id=data.get("podbean_episode_id", ""),
            podbean_episode_url=data.get("podbean_episode_url", ""),
        )
