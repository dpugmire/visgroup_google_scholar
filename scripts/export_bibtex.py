#!/usr/bin/env python3
"""Export generated publication data as BibTeX."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "with",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as in_file:
        return json.load(in_file)


def normalize_spaces(value: str) -> str:
    value = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def ascii_token(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "", value)


def title_tokens(title: str) -> list[str]:
    tokens = []
    for raw in re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", title)):
        token = ascii_token(raw)
        if not token or token.lower() in STOP_WORDS:
            continue
        tokens.append(token[:24])
    return tokens


def bibtex_key(pub: dict, used_keys: set[str]) -> str:
    authors = [name for name in pub.get("author_names", []) if name and name != "..."]
    first_author = authors[0] if authors else "unknown"
    last_name = ascii_token(first_author.split()[-1]) or "unknown"
    year = str(pub.get("year") or "nd")
    title_part = "".join(token[:1].upper() + token[1:] for token in title_tokens(pub.get("title", ""))[:3])
    base = f"{last_name}{year}{title_part}" or "publication"

    key = base
    suffix = 2
    while key in used_keys:
        key = f"{base}_{suffix}"
        suffix += 1
    used_keys.add(key)
    return key


def entry_type(pub: dict) -> str:
    text = " ".join([pub.get("type", ""), pub.get("venue", ""), pub.get("title", "")]).lower()
    if "patent" in text or "software" in text:
        return "misc"
    if "conference" in text or "proceedings" in text or "workshop" in text or "symposium" in text:
        return "inproceedings"
    if "book" in text:
        return "book"
    return "article"


def escape_bibtex(value: str) -> str:
    value = normalize_spaces(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def clean_authors(author_names: list[str]) -> str:
    names = [normalize_spaces(name) for name in author_names if normalize_spaces(name) and name != "..."]
    return " and ".join(names)


def bibtex_fields(pub: dict, kind: str) -> list[tuple[str, str]]:
    fields = []
    authors = clean_authors(pub.get("author_names", []))
    venue = normalize_spaces(pub.get("venue", ""))
    url = normalize_spaces(pub.get("doi_url") or pub.get("url", ""))

    if authors:
        fields.append(("author", authors))
    if pub.get("title"):
        fields.append(("title", pub["title"]))
    if pub.get("year"):
        fields.append(("year", str(pub["year"])))
    if venue:
        if kind == "inproceedings":
            fields.append(("booktitle", venue))
        elif kind == "misc":
            fields.append(("howpublished", venue))
        else:
            fields.append(("journal", venue))
    if pub.get("doi"):
        fields.append(("doi", pub["doi"]))
    if url:
        fields.append(("url", url))
    return fields


def publication_to_bibtex(pub: dict, used_keys: set[str]) -> str:
    kind = entry_type(pub)
    key = bibtex_key(pub, used_keys)
    lines = [f"@{kind}{{{key},"]
    fields = bibtex_fields(pub, kind)
    for index, (name, value) in enumerate(fields):
        comma = "," if index < len(fields) - 1 else ""
        lines.append(f"  {name} = {{{escape_bibtex(value)}}}{comma}")
    lines.append("}")
    return "\n".join(lines)


def export_bibtex(publications: list[dict]) -> str:
    used_keys: set[str] = set()
    entries = [publication_to_bibtex(pub, used_keys) for pub in publications]
    return "\n\n".join(entries) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publications", type=Path, default=Path("site/data/publications.json"))
    parser.add_argument("--out", type=Path, default=Path("site/data/publications.bib"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    data = load_json(args.publications)
    publications = data.get("publications", [])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(export_bibtex(publications), encoding="utf-8")
    print(f"Wrote {len(publications)} BibTeX entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
