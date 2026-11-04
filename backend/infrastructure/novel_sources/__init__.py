"""Import novel chapters from web sources (Qidian first; Fanqie and Jinjiang planned)."""

from infrastructure.novel_sources.base import NovelSourceError
from infrastructure.novel_sources.browser import CdpPageRunner
from infrastructure.novel_sources.qidian import QidianSource
from infrastructure.novel_sources.service import NovelImportService

__all__ = ["CdpPageRunner", "NovelImportService", "NovelSourceError", "QidianSource"]
