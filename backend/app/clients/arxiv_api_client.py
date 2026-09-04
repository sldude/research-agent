"""Small client for retrieving paper metadata from arXiv's Atom API.

This module intentionally does not write to DynamoDB or call Bedrock. Its
only responsibility is to request metadata and convert XML entries into
validated ``ArxivPaper`` objects.
"""

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

import httpx

from app.schemas.api_schemas import ArxivPaper


ARXIV_API_URL = "https://export.arxiv.org/api/query"

# arXiv returns an Atom feed. ElementTree needs these namespace mappings to
# locate tags such as ``atom:title`` and ``arxiv:license`` correctly.
NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


class ArxivAPIError(RuntimeError):
    """Raised when arXiv cannot be reached or returns unusable data."""


class ArxivClient:
    """Retrieve and normalize arXiv metadata while respecting rate limits."""

    # These values are shared by all ArxivClient instances in this process.
    # The lock serializes calls, and the timestamp keeps calls three seconds
    # apart. A multi-process deployment will eventually need a shared limiter.
    _request_lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(
        self,
        *,
        user_agent: str = "research-agent/0.1 (contact: steven.r.liu20@gmail.com)",
        request_interval_seconds: float = 3.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        if request_interval_seconds < 0:
            raise ValueError("request_interval_seconds cannot be negative")

        self.request_interval_seconds = request_interval_seconds

        # Reusing one httpx.Client also reuses its underlying network connection.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        """Release network resources held by the reusable HTTP client."""

        self._client.close()

    def __enter__(self) -> "ArxivClient":
        """Allow usage as ``with ArxivClient() as client:``."""

        return self

    def __exit__(self, *_: object) -> None:
        """Always close the HTTP client when leaving a ``with`` block."""

        self.close()

    def search(
        self,
        query: str,
        *,
        start: int = 0,
        max_results: int = 10,
    ) -> list[ArxivPaper]:
        """Search all arXiv metadata fields for a user-provided topic.

        This convenience method changes ``graph neural networks`` into the
        arXiv expression ``all:graph neural networks``. Use ``search_advanced``
        when field filters or Boolean operators are needed.
        """

        topic = query.strip()
        if not topic:
            raise ValueError("query cannot be empty")

        return self.search_advanced(
            f"all:{topic}",
            start=start,
            max_results=max_results,
        )

    def search_advanced(
        self,
        search_query: str,
        *,
        start: int = 0,
        max_results: int = 10,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> list[ArxivPaper]:
        """Run an arXiv query expression and return normalized papers.

        Example expressions include ``cat:cs.AI`` and
        ``all:retrieval AND cat:cs.IR``. This method should receive a trusted
        expression built by the backend rather than arbitrary URL parameters.
        """

        search_query = search_query.strip()
        if not search_query:
            raise ValueError("search_query cannot be empty")
        if start < 0:
            raise ValueError("start cannot be negative")
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        if sort_by not in {"relevance", "lastUpdatedDate", "submittedDate"}:
            raise ValueError("unsupported sort_by value")
        if sort_order not in {"ascending", "descending"}:
            raise ValueError("sort_order must be ascending or descending")

        parameters: dict[str, Any] = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }

        response = self._get(parameters)

        try:
            feed = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ArxivAPIError("arXiv returned invalid XML") from exc

        entries = feed.findall("atom:entry", NAMESPACES)

        # Some arXiv API errors are represented as an Atom entry rather than
        # an HTTP error, so detect that response before parsing normal papers.
        if len(entries) == 1:
            title = self._optional_text(entries[0], "atom:title")
            if title and title.casefold() == "error":
                detail = self._optional_text(entries[0], "atom:summary")
                raise ArxivAPIError(detail or "arXiv rejected the query")

        return [self._parse_entry(entry) for entry in entries]

    def _get(self, parameters: dict[str, Any]) -> httpx.Response:
        """Make one serialized, rate-limited API request."""

        with type(self)._request_lock:
            elapsed = time.monotonic() - type(self)._last_request_time
            wait_seconds = self.request_interval_seconds - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            try:
                response = self._client.get(ARXIV_API_URL, params=parameters)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                raise ArxivAPIError(
                    f"Could not retrieve results from arXiv: {exc}"
                ) from exc
            finally:
                # Record failed attempts too, so repeated failures cannot cause
                # a rapid retry loop against arXiv.
                type(self)._last_request_time = time.monotonic()

    def _parse_entry(self, entry: ET.Element) -> ArxivPaper:
        """Convert one Atom ``entry`` element into an ArxivPaper."""

        entry_url = self._required_text(entry, "atom:id")
        external_id = self._external_id(entry_url)

        authors = [
            self._normalize_whitespace(name.text)
            for name in entry.findall("atom:author/atom:name", NAMESPACES)
            if name.text and name.text.strip()
        ]
        categories = [
            category.attrib["term"]
            for category in entry.findall("atom:category", NAMESPACES)
            if category.attrib.get("term")
        ]

        return ArxivPaper(
            external_id=external_id,
            title=self._normalize_whitespace(
                self._required_text(entry, "atom:title")
            ),
            abstract=self._normalize_whitespace(
                self._required_text(entry, "atom:summary")
            ),
            authors=authors,
            publication_date=self._parse_date(
                self._optional_text(entry, "atom:published")
            ),
            updated_date=self._parse_date(
                self._optional_text(entry, "atom:updated")
            ),
            source_url=self._alternate_url(entry, entry_url),
            categories=categories,
            # This extension is not guaranteed to be present, so the database
            # and API schema deliberately allow it to be null.
            license_url=self._optional_text(entry, "arxiv:license"),
        )

    @staticmethod
    def _optional_text(entry: ET.Element, path: str) -> str | None:
        """Read text from an XML element, returning None when absent."""

        element = entry.find(path, NAMESPACES)
        if element is None or element.text is None:
            return None

        value = element.text.strip()
        return value or None

    @classmethod
    def _required_text(cls, entry: ET.Element, path: str) -> str:
        """Read a required XML field or report a malformed entry."""

        value = cls._optional_text(entry, path)
        if value is None:
            raise ArxivAPIError(f"An arXiv result is missing {path}")
        return value

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        """Replace newlines and repeated spaces with single spaces."""

        return " ".join(value.split())

    @staticmethod
    def _external_id(entry_url: str) -> str:
        """Extract an ID while preserving old IDs such as hep-ex/0307015."""

        marker = "/abs/"
        if marker not in entry_url:
            raise ArxivAPIError(f"Unexpected arXiv entry URL: {entry_url}")

        external_id = entry_url.split(marker, maxsplit=1)[1]

        # v1, v2, and later versions refer to the same logical document row.
        return re.sub(r"v\d+$", "", external_id)

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        """Convert an ISO-8601 timestamp into a date for storage."""

        if value is None:
            return None

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError as exc:
            raise ArxivAPIError(f"arXiv returned an invalid date: {value}") from exc

    @staticmethod
    def _alternate_url(entry: ET.Element, fallback_url: str) -> str:
        """Return the abstract-page link, never the PDF download link."""

        for link in entry.findall("atom:link", NAMESPACES):
            if link.attrib.get("rel") == "alternate":
                href = link.attrib.get("href")
                if href:
                    return href.replace("http://", "https://", 1)

        return fallback_url.replace("http://", "https://", 1)
