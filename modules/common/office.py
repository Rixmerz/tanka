"""Plain text of Office files, for assistants whose Read tool cannot open them. Standard library only."""
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def office_text(path: Path) -> str | None:
    """Plain text of an .xlsx or .docx, because the assistant's Read cannot open them."""
    try:
        with zipfile.ZipFile(path) as z:
            if path.suffix == ".docx":
                w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                root = ET.fromstring(z.read("word/document.xml"))
                return "\n".join("".join(t.text or "" for t in p.iter(f"{w}t")) for p in root.iter(f"{w}p")).strip()
            if path.suffix == ".xlsx":
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                shared = []
                if "xl/sharedStrings.xml" in z.namelist():
                    shared = ["".join(t.text or "" for t in si.iter(f"{ns}t"))
                              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{ns}si")]
                rows = []
                for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
                    rows.append(f"## {Path(name).stem}")
                    for row in ET.fromstring(z.read(name)).iter(f"{ns}row"):
                        cells = []
                        for c in row.iter(f"{ns}c"):
                            v = c.find(f"{ns}v")
                            cells.append(shared[int(v.text)] if c.get("t") == "s" and v is not None
                                         else (v.text if v is not None else ""))
                        if any(cells):
                            rows.append(" | ".join(cells))
                return "\n".join(rows)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, IndexError):
        return None
    return None
