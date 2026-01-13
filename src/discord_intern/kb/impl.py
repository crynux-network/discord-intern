from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Sequence, Set
from urllib.parse import urlparse

import aiohttp

from discord_intern.ai.interfaces import AIClient
from discord_intern.config.models import KnowledgeBaseSettings
from discord_intern.kb.interfaces import IndexEntry, SourceContent

logger = logging.getLogger(__name__)


class FileSystemKnowledgeBase:
    def __init__(self, config: KnowledgeBaseSettings, ai_client: AIClient):
        self.config = config
        self.ai_client = ai_client
        self._url_pattern = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*')

    async def load_index_text(self) -> str:
        """Load the startup-produced index artifact as plain text."""
        index_path = Path(self.config.index_path)
        if not index_path.exists():
            return ""
        return index_path.read_text(encoding="utf-8")

    async def load_index_entries(self) -> Sequence[IndexEntry]:
        """Load the startup-produced index artifact as structured entries."""
        text = await self.load_index_text()
        entries = []
        if not text:
            return entries

        # Split by double newlines to separate entries
        chunks = text.strip().split("\n\n")
        for chunk in chunks:
            lines = chunk.strip().split("\n")
            if not lines:
                continue
            source_id = lines[0].strip()
            description = "\n".join(lines[1:]).strip()
            entries.append(IndexEntry(source_id=source_id, description=description))
        return entries

    async def build_index(self) -> None:
        """Build the startup index artifact on disk."""
        logger.info("kb.build_index_start")
        sources_dir = Path(self.config.sources_dir)
        if not sources_dir.exists():
            logger.warning("kb.sources_dir_missing path=%s", sources_dir)
            return

        # 1. Gather sources
        file_sources: Set[Path] = set()
        url_sources: Set[str] = set()

        for file_path in sources_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                try:
                    text = file_path.read_text(encoding="utf-8")
                    file_sources.add(file_path)
                    # Extract URLs
                    found_urls = self._url_pattern.findall(text)
                    for url in found_urls:
                        # Simple cleanup of trailing punctuation
                        url = url.rstrip('.,;)"\'')
                        url_sources.add(url)
                except UnicodeDecodeError:
                    logger.warning("kb.file_decode_error path=%s", file_path)
                    continue

        logger.info("kb.sources_found files=%d urls=%d", len(file_sources), len(url_sources))

        # 2. Process sources and generate summaries
        entries: list[str] = []

        # Process files
        for file_path in sorted(file_sources):
            try:
                text = file_path.read_text(encoding="utf-8")
                rel_path = file_path.relative_to(sources_dir).as_posix()
                summary = await self.ai_client.summarize_for_kb_index(
                    source_id=rel_path,
                    text=text,
                    timeout_seconds=30.0 # Using a default timeout, ideally from config
                )
                entries.append(f"{rel_path}\n{summary}")
            except Exception as e:
                logger.error("kb.file_processing_error path=%s error=%s", file_path, e)

        # Process URLs
        # Ensure cache dir exists
        cache_dir = Path(self.config.web_fetch_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        async with aiohttp.ClientSession() as session:
            for url in sorted(url_sources):
                try:
                    text = await self._fetch_url(session, url, cache_dir)
                    if not text:
                        continue

                    summary = await self.ai_client.summarize_for_kb_index(
                        source_id=url,
                        text=text,
                        timeout_seconds=30.0
                    )
                    entries.append(f"{url}\n{summary}")
                except Exception as e:
                    logger.error("kb.url_processing_error url=%s error=%s", url, e)

        # 3. Write index
        index_path = Path(self.config.index_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)

        # Join with blank lines
        index_content = "\n\n".join(entries)
        index_path.write_text(index_content, encoding="utf-8")
        logger.info("kb.index_written path=%s entries=%d", index_path, len(entries))

    async def load_source_content(self, *, source_id: str) -> SourceContent:
        """Load full source content for a file path or URL identifier."""
        sources_dir = Path(self.config.sources_dir)

        # Check if it's a URL
        if source_id.startswith(("http://", "https://")):
             cache_dir = Path(self.config.web_fetch_cache_dir)
             # We need to re-fetch or read from cache.
             # For load_source_content, we prefer cache but might need to fetch if missing.
             # Ideally build_index ensures cache is populated.
             # Here we reuse the fetch logic.
             async with aiohttp.ClientSession() as session:
                 text = await self._fetch_url(session, source_id, cache_dir)
                 return SourceContent(source_id=source_id, text=text)

        # Assume file path relative to sources_dir
        file_path = sources_dir / source_id
        try:
            if file_path.exists() and file_path.is_file():
                 text = file_path.read_text(encoding="utf-8")
                 return SourceContent(source_id=source_id, text=text)
        except Exception as e:
            logger.warning("kb.load_file_error path=%s error=%s", file_path, e)

        return SourceContent(source_id=source_id, text="")

    async def _fetch_url(self, session: aiohttp.ClientSession, url: str, cache_dir: Path) -> str:
        """Fetch URL content, using cache if available."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        cache_file = cache_dir / url_hash

        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

        try:
            timeout = aiohttp.ClientTimeout(total=self.config.web_fetch_timeout_seconds)
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    logger.warning("kb.fetch_error url=%s status=%s", url, response.status)
                    return ""

                # Check size
                content = await response.read()
                if len(content) > self.config.max_source_bytes:
                    logger.warning("kb.fetch_too_large url=%s size=%d", url, len(content))
                    return ""

                # Decode
                text = content.decode("utf-8", errors="replace")

                # Cache it
                cache_file.write_text(text, encoding="utf-8")
                return text
        except Exception as e:
            logger.warning("kb.fetch_exception url=%s error=%s", url, e)
            return ""
