from __future__ import annotations

from io import StringIO
from typing import Dict, List

import pandas as pd


class HTMLTableParser:
    """
    Production wrapper around pandas.read_html().

    Features
    --------
    ✓ Parses all tables
    ✓ Handles rowspan
    ✓ Handles colspan
    ✓ Flattens MultiIndex headers
    ✓ Forward fills rowspan values
    ✓ Removes empty rows/columns
    ✓ Normalizes whitespace
    ✓ Returns DataFrame or JSON
    """

    def __init__(
        self,
        forward_fill: bool = True,
        drop_empty_rows: bool = True,
        drop_empty_cols: bool = True,
        flatten_headers: bool = True,
    ):
        self.forward_fill = forward_fill
        self.drop_empty_rows = drop_empty_rows
        self.drop_empty_cols = drop_empty_cols
        self.flatten_headers = flatten_headers

    # -----------------------------------------------------
    # Public APIs
    # -----------------------------------------------------

    def parse_html(
        self,
        html: str,
    ) -> List[pd.DataFrame]:

        return self._parse_tables(html)

    def parse_table(
        self,
        table,
    ) -> pd.DataFrame:

        html = str(table)

        return self._parse_tables(html)[0]

    def parse_to_records(
        self,
        html: str,
    ) -> List[List[Dict]]:

        dfs = self._parse_tables(html)

        return [df.to_dict(orient="records") for df in dfs]

    # -----------------------------------------------------
    # Internal
    # -----------------------------------------------------

    def _parse_tables(self, html: str):

        dfs = pd.read_html(StringIO(html))

        cleaned = []

        for df in dfs:
            df = self._clean_dataframe(df)

            cleaned.append(df)

        return cleaned

    def _clean_dataframe(self, df: pd.DataFrame):

        # ----------------------------
        # Flatten MultiIndex
        # ----------------------------
        if self.flatten_headers and isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                " > ".join(
                    str(x).strip()
                    for x in col
                    if pd.notna(x) and str(x).strip() and "Unnamed" not in str(x)
                )
                for col in df.columns
            ]

        else:
            df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

        # ----------------------------
        # Normalize cells
        # ----------------------------

        df = df.map(
            lambda x: str(x).replace("\xa0", " ").strip() if pd.notna(x) else None
        )

        # ----------------------------
        # Forward fill rowspan cells
        # ----------------------------

        if self.forward_fill:
            df = df.ffill()

        # ----------------------------
        # Remove empty rows
        # ----------------------------

        if self.drop_empty_rows:
            df = df.dropna(how="all")

        # ----------------------------
        # Remove empty columns
        # ----------------------------

        if self.drop_empty_cols:
            df = df.dropna(axis=1, how="all")

        return df.reset_index(drop=True)

    # -----------------------------------------------------
    # Helpers
    # -----------------------------------------------------

    @staticmethod
    def dataframe_to_markdown(df: pd.DataFrame):

        return df.to_markdown(index=False)

    @staticmethod
    def dataframe_to_records(df: pd.DataFrame):

        return df.to_dict(orient="records")


with open("experiments/artifacts/table_html_4.html.md", "r") as f:
    html = f.read()
parser = HTMLTableParser()
dfs = parser.parse_html(html)

for i, df in enumerate(dfs, 1):
    print(f"\n--------TABLE {i} --------")
    print(df)
