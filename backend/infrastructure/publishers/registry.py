from __future__ import annotations

from domain.social_publishing import SocialPlatform, SocialPublisher


class PublisherNotConfiguredError(LookupError):
    pass


class PublisherRegistry:
    def __init__(self, publishers: list[SocialPublisher] | None = None) -> None:
        self._publishers: dict[SocialPlatform, SocialPublisher] = {}
        for publisher in publishers or []:
            self.register(publisher)

    def register(self, publisher: SocialPublisher) -> None:
        platform = SocialPlatform(publisher.platform)
        if platform in self._publishers:
            raise ValueError(f"Publisher {platform.value} đã được đăng ký")
        self._publishers[platform] = publisher

    def get(self, platform: SocialPlatform | str) -> SocialPublisher:
        normalized = SocialPlatform(platform)
        try:
            return self._publishers[normalized]
        except KeyError as exc:
            raise PublisherNotConfiguredError(
                f"Nền tảng {normalized.value} chưa được cấu hình"
            ) from exc

    def capabilities(self) -> list[dict[str, object]]:
        return [
            {"platform": platform.value, **vars(publisher.capabilities)}
            for platform, publisher in sorted(
                self._publishers.items(), key=lambda item: item[0].value
            )
        ]

