"""Чья сеть у этого IP: ASN и тип (мобильная или нет), без запросов к сторонним сервисам.

Диапазоны в data/networks.tsv.gz собирает scripts/build_networks.py, какие ASN мобильные,
записано вручную в data/network-kinds.json.
"""

import gzip
import ipaddress
import json
from bisect import bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RANGES = ROOT / "data" / "networks.tsv.gz"
KINDS = ROOT / "data" / "network-kinds.json"

_starts = []
_ends = []
_asns = []
_known = {}
_loaded = False


def _load():
    global _loaded
    if _loaded:
        return
    if RANGES.exists():
        with gzip.open(RANGES, "rt", encoding="utf-8") as source:
            for line in source:
                start, end, asn = line.rstrip("\n").split("\t")
                _starts.append(int(start))
                _ends.append(int(end))
                _asns.append(int(asn))
    if KINDS.exists():
        _known.update(json.loads(KINDS.read_text(encoding="utf-8"))["asns"])
    _loaded = True


def lookup(ip: str):
    """{"asn": число или None, "name": подпись, "kind": "mobile" | "mixed" | "home" | None}."""
    _load()
    asn = None
    try:
        value = int(ipaddress.ip_address(ip))
    except ValueError:
        value = None
    if value is not None:
        at = bisect_right(_starts, value) - 1
        if at >= 0 and value <= _ends[at]:
            asn = _asns[at]
    info = _known.get(str(asn)) if asn else None
    return {
        "asn": asn,
        "name": info["name"] if info else (f"AS{asn}" if asn else ""),
        "kind": info["kind"] if info else None,
    }
