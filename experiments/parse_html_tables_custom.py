from bs4 import BeautifulSoup
from bs4.element import Tag


class TableParser:
    # -----------------------------
    # Public API
    # -----------------------------
    def parse(self, table: Tag):

        matrix = self._expand_table(table)

        header_count = self._detect_header_rows(matrix)

        header_matrix = matrix[:header_count]
        body_matrix = matrix[header_count:]

        headers = self._build_headers(header_matrix)

        records = []

        for row in body_matrix:
            row = row + [""] * (len(headers) - len(row))

            records.append({headers[i]: row[i].strip() for i in range(len(headers))})

        return headers, records

    # -----------------------------
    # Expand rowspan / colspan
    # -----------------------------
    def _expand_table(self, table):

        rows = table.find_all("tr")

        matrix = []

        span_map = {}

        for r, tr in enumerate(rows):
            row = []

            col = 0

            while True:
                if (r, col) not in span_map:
                    break

                row.append(span_map[(r, col)])

                del span_map[(r, col)]

                col += 1

            cells = tr.find_all(["td", "th"])

            for cell in cells:
                while (r, col) in span_map:
                    row.append(span_map[(r, col)])

                    del span_map[(r, col)]

                    col += 1

                value = " ".join(cell.stripped_strings)

                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))

                for c in range(colspan):
                    row.append(value)

                    if rowspan > 1:
                        for rr in range(1, rowspan):
                            span_map[(r + rr, col)] = value

                    col += 1

            matrix.append(row)

        max_cols = max(len(r) for r in matrix)

        for r in matrix:
            r.extend([""] * (max_cols - len(r)))

        return matrix

    # -----------------------------
    # Detect header rows
    # -----------------------------
    def _detect_header_rows(self, matrix):

        header_rows = 0

        for row in matrix:
            non_empty = [c for c in row if c.strip()]

            numeric = sum(any(ch.isdigit() for ch in cell) for cell in non_empty)

            # looks like data
            if numeric >= len(non_empty) // 2:
                break

            header_rows += 1

        return max(header_rows, 1)

    # -----------------------------
    # Build hierarchical headers
    # -----------------------------
    def _build_headers(self, header_matrix):

        cols = len(header_matrix[0])

        headers = []

        for c in range(cols):
            pieces = []

            previous = ""

            for r in range(len(header_matrix)):
                value = header_matrix[r][c].strip()

                if not value:
                    continue

                if value == previous:
                    continue

                pieces.append(value)

                previous = value

            if not pieces:
                pieces.append(f"Column {c + 1}")

            headers.append(" > ".join(pieces))

        return headers


if __name__ == "__main__":
    from bs4 import BeautifulSoup

    for i in range(1, 4):
        with open(r"experiments\artifacts\table_html_{}.html.md".format(i)) as f:
            html = f.read()

            soup = BeautifulSoup(html, "lxml")

        table = soup.find("table")

        parser = TableParser()
        print(f"--------TABLE {i} --------")
        records = parser.parse(table)

        for r in records:
            print(r)

        print()
