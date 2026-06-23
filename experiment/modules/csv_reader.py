import csv
from pathlib import Path
from typing import Iterator


class CsvReader:
    def __init__(self, filepath: str | Path) -> None:
        self.filepath = Path(filepath)

    def rows(self) -> Iterator[dict[str, str]]:
        with self.filepath.open(newline="") as fh:
            yield from csv.DictReader(fh)

    def read_all(self) -> list[dict[str, str]]:
        return list(self.rows())

    def count(self) -> int:
        return sum(1 for _ in self.rows())

    def column(self, name: str) -> list[str]:
        return [row[name] for row in self.rows() if name in row]
