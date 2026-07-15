import asyncio
import logging
import re
from typing import List, Dict, Any, Tuple
from bs4 import BeautifulSoup
import tiktoken
from app.ingest.parser import HTMLTableParser
from app.configs.ingest import settings as ingest_settings

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
        encoding_name: str = "cl100k_base",
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.encoding_name = encoding_name
        self.table_parser = HTMLTableParser()
        # Read targeted container selector from config settings
        self.target_html_selector = ingest_settings.target_html_selector

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

        # If target CSS selector is configured, isolate only the targeted container content
        if self.target_html_selector:
            target_el = soup.select_one(self.target_html_selector)
            if target_el:
                soup = BeautifulSoup(str(target_el), "lxml")
            else:
                logger.warning(
                    "Target HTML selector '%s' not found in document. Falling back to parsing whole page content.",
                    self.target_html_selector,
                )

        table_chunks: List[Dict[str, Any]] = []

        # Determine table titles from surrounding contexts
        title_prefix = f"Table from: {source_title} | " if source_title else ""

        table_tags = soup.find_all("table")
        for i, table_tag in enumerate(table_tags):
            try:
                # Parse using pandas wrapper
                df = self.table_parser.parse_table(table_tag)
                if df.empty:
                    continue

                table_title = self._infer_table_title(table_tag, i)
                context_str = f"{title_prefix}Table: {table_title}"

                # 1. Serialize row-by-row for high-density semantic search
                records = self.table_parser.dataframe_to_records(df)
                for row_idx, row in enumerate(records):
                    # Format as: ColumnA: ValueA | ColumnB: ValueB
                    row_details = " | ".join(
                        f"{col}: {val}" for col, val in row.items() if pd_not_null(val)
                    )
                    content = f"{context_str}\nRow {row_idx + 1}: {row_details}"

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

                # 2. Replace table tag in raw HTML with a simple marker text to clear it out
                table_tag.replace_with(
                    soup.new_string(f" [Refer to Table: {table_title}] ")
                )
            except Exception:
                # Fallback: remove table tag on exception to prevent raw html tags leak
                table_tag.decompose()

        # Extract remaining cleaned text from BS4 DOM
        cleaned_text = soup.get_text(separator="\n")
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
