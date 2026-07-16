import asyncio
import logging
import re
from typing import Any, Dict, List, Tuple

import tiktoken
from bs4 import BeautifulSoup
import html2text

from app.configs.ingest import settings as ingest_settings
from app.ingest.parser import HTMLTableParser

logger = logging.getLogger(__name__)


class TokenAwareChunker:
    """
    Splits document body text into token-bounded chunks and extracts HTML tables
    into highly dense semantic rows to optimize embedding density and prevent truncation.
    """

    def __init__(
        self,
        chunk_size: int = 400,
        chunk_overlap: int = 50,
        encoding_name: str = "o200k_harmony",
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.encoding_name = encoding_name
        self.table_parser = HTMLTableParser()
        # Read targeted container selector from config settings
        self.target_html_selector = ingest_settings.target_html_selector
        self.config = getattr(ingest_settings, "config", {})

    async def chunk_document(
        self,
        text_or_html: str,
        is_html: bool = False,
        source_title: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Main entrypoint. To prevent blocking the event loop on large text or table structures,
        we offload the parsing and token splitting routines to a worker thread.
        """
        return await asyncio.to_thread(
            self._chunk_document_sync, text_or_html, is_html, source_title
        )

    def _chunk_document_sync(
        self,
        text_or_html: str,
        is_html: bool = False,
        source_title: str = None,
    ) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []

        if not text_or_html:
            return chunks

        if is_html:
            # 1. Parse HTML, extract tables, and get cleaned text
            cleaned_text, tables_chunks = self._process_html_tables_and_text(
                text_or_html, source_title
            )
            chunks.extend(tables_chunks)
            # 2. Chunk remaining non-table text content
            text_chunks = self._chunk_plain_text(
                cleaned_text, source_title, start_index=len(chunks)
            )
            chunks.extend(text_chunks)
        else:
            # Plain text document ingestion
            chunks.extend(
                self._chunk_plain_text(text_or_html, source_title, start_index=0)
            )

        return chunks

    def _process_html_tables_and_text(
        self,
        html_content: str,
        source_title: str = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        soup = BeautifulSoup(html_content, "lxml")

        # 1. Prune Excluded elements globally first
        exclude = self.config.get("exclude", {})
        for tag in exclude.get("tags", []):
            for el in soup.find_all(tag):
                el.decompose()
        
        # Substring regex matching for classes to handle Next.js CSS Modules
        for cls in exclude.get("classes", []):
            pattern = re.compile(re.escape(cls), re.IGNORECASE)
            for el in soup.find_all(class_=pattern):
                el.decompose()
                
        # Substring regex matching for IDs
        for eid in exclude.get("ids", []):
            pattern = re.compile(re.escape(eid), re.IGNORECASE)
            for el in soup.find_all(id=pattern):
                el.decompose()

        # 2. Find Root Container
        root_selectors = self.config.get("root_selectors", [])
        if not root_selectors and self.target_html_selector:
            root_selectors = [self.target_html_selector]

        root_el = None
        for sel in root_selectors:
            match = soup.select_one(sel)
            if match:
                root_el = match
                break
        if root_el:
            soup = BeautifulSoup(str(root_el), "lxml")

        # 3. Apply Inclusions (strategies: first_match / all_matches)
        include = self.config.get("include", {})
        strategy = include.get("strategy", "first_match")
        include_selectors = include.get("selectors", [])

        if include_selectors:
            if strategy == "first_match":
                included_el = None
                for sel in include_selectors:
                    match = soup.select_one(sel)
                    if match:
                        included_el = match
                        break
                if included_el:
                    soup = BeautifulSoup(str(included_el), "lxml")
            elif strategy == "all_matches":
                matches = []
                for sel in include_selectors:
                    found = soup.select(sel)
                    if found:
                        matches.extend(found)
                if matches:
                    combined = "".join(str(m) for m in matches)
                    soup = BeautifulSoup(combined, "lxml")

        # Prune final DOM one more time to strip nested matching exclusions
        for tag in exclude.get("tags", []):
            for el in soup.find_all(tag):
                el.decompose()
        for cls in exclude.get("classes", []):
            pattern = re.compile(re.escape(cls), re.IGNORECASE)
            for el in soup.find_all(class_=pattern):
                el.decompose()

        # Proceed with normal Table Extraction using base class code
        table_chunks: List[Dict[str, Any]] = []
        title_prefix = f"Table from: {source_title} | " if source_title else ""

        table_tags = soup.find_all("table")
        for i, table_tag in enumerate(table_tags):
            try:
                # Parse using pandas wrapper
                df = self.table_parser.parse_table(table_tag)
                if df.empty:
                    continue

                table_title = self._infer_table_title(table_tag, i)
                subtitle = df.attrs.get("table_subtitle")
                subtitle_str = f" - {subtitle}" if subtitle else ""
                context_str = f"{title_prefix}Table: {table_title}{subtitle_str}"

                # Serialize row-by-row as a mini markdown table snippet
                records = self.table_parser.dataframe_to_records(df)
                for row_idx, row in enumerate(records):
                    # Filter out empty/null values for this specific row
                    active_cols = [col for col, val in row.items() if pd_not_null(val)]
                    active_vals = [str(row[col]).strip() for col in active_cols]

                    if active_cols:
                        headers_line = " | ".join(active_cols)
                        divider_line = " | ".join("---" for _ in active_cols)
                        values_line = " | ".join(active_vals)

                        table_md = f"| {headers_line} |\n| {divider_line} |\n| {values_line} |"
                        content = f"{context_str}\nRow {row_idx + 1}:\n{table_md}"

                        table_chunks.append(
                            {
                                "content": content,
                                "metadata": {
                                    "type": "table_row",
                                    "table_index": i,
                                    "row_index": row_idx,
                                    "table_title": table_title,
                                },
                            }
                        )

                # Replace table tag in raw HTML with a simple marker text
                table_tag.replace_with(
                    soup.new_string(f" [Refer to Table: {table_title}] ")
                )
            except Exception:
                # Fallback: remove table tag on exception to prevent raw html tags leak
                table_tag.decompose()

        # Extract remaining cleaned text using html2text configured with settings
        html_conv_config = self.config.get("html_converter", {})
        converter = html2text.HTML2Text()
        converter.ignore_images = html_conv_config.get("ignore_images", True)
        converter.ignore_emphasis = html_conv_config.get("ignore_emphasis", True)
        converter.ignore_links = html_conv_config.get("ignore_links", True)
        converter.body_width = html_conv_config.get("body_width", 0)

        cleaned_text = converter.handle(str(soup))
        # Collapse multiple empty newlines
        cleaned_text = re.sub(r"\n\s*\n+", "\n\n", cleaned_text).strip()

        return cleaned_text, table_chunks

    def _chunk_plain_text(
        self,
        text: str,
        source_title: str = None,
        start_index: int = 0,
    ) -> List[Dict[str, Any]]:
        chunks = []
        if not text.strip():
            return chunks

        encoding = tiktoken.get_encoding(self.encoding_name)
        tokens = encoding.encode(text)

        idx = start_index
        start = 0
        while start < len(tokens):
            end = start + self.chunk_size
            chunk_tokens = tokens[start:end]
            chunk_text = encoding.decode(chunk_tokens).strip()

            if chunk_text:
                # Add contextual title at the top of each text chunk if available
                content = (
                    f"Document: {source_title}\n\n{chunk_text}"
                    if source_title
                    else chunk_text
                )
                chunks.append(
                    {
                        "content": content,
                        "metadata": {
                            "type": "text",
                            "chunk_index": idx,
                        },
                    }
                )
                idx += 1

            if end >= len(tokens):
                break
            start += self.chunk_size - self.chunk_overlap

        return chunks

    def _infer_table_title(self, table_tag, index: int) -> str:
        # Check if table has caption
        caption = table_tag.find("caption")
        if caption and caption.text.strip():
            return caption.text.strip()

        # Check preceding element (e.g. h2, h3, p) for context titles
        sibling = table_tag.find_previous(["h1", "h2", "h3", "h4", "p"])
        if sibling and sibling.text.strip():
            title_text = sibling.text.strip()
            # Truncate length
            return title_text[:100]

        return f"Dataset Table {index + 1}"


def pd_not_null(val: Any) -> bool:
    import pandas as pd

    if val is None:
        return False
    val_str = str(val).strip().lower()
    return pd.notna(val) and val_str not in ("nan", "none", "null", "")
