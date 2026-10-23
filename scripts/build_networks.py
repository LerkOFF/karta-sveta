#!/usr/bin/env python3
"""Собирает data/networks.tsv.gz: диапазоны IPv4 российских и украинских сетей и номер ASN.

Нужно, чтобы по IP человека, подключающего датчик, узнать его сеть (ASN) без запросов к
сторонним сервисам. Источник: https://iptoasn.com/data/ip2asn-v4.tsv.gz (общественное достояние).

    curl -sL -o /tmp/ip2asn-v4.tsv.gz https://iptoasn.com/data/ip2asn-v4.tsv.gz
    python3 scripts/build_networks.py /tmp/ip2asn-v4.tsv.gz

Каждая строка результата: начало, конец (числом) и ASN. Соседние диапазоны одного ASN склеиваются.
Какие ASN мобильные, записано вручную в data/network-kinds.json.
"""

import gzip
import ipaddress
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "networks.tsv.gz"
COUNTRIES = ("RU", "UA")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    merged = []
    with gzip.open(sys.argv[1], "rt", encoding="utf-8") as source:
        for line in source:
            start, end, asn, country, *_ = line.rstrip("\n").split("\t")
            if country not in COUNTRIES or asn == "0":
                continue
            first, last = int(ipaddress.ip_address(start)), int(ipaddress.ip_address(end))
            if merged and merged[-1][2] == asn and merged[-1][1] + 1 == first:
                merged[-1][1] = last
            else:
                merged.append([first, last, asn])
    body = "\n".join(f"{first}\t{last}\t{asn}" for first, last, asn in merged).encode("utf-8")
    OUT.write_bytes(gzip.compress(body, 9, mtime=0))
    print(f"{len(merged)} диапазонов, {OUT.stat().st_size // 1024} КБ: {OUT}")


if __name__ == "__main__":
    main()
