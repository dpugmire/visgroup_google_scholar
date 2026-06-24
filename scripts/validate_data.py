#!/usr/bin/env python3
"""Validate generated publication data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def h_index(citations: list[int]) -> int:
    h = 0
    for rank, count in enumerate(sorted(citations, reverse=True), start=1):
        if count >= rank:
            h = rank
        else:
            break
    return h


def i10_index(citations: list[int]) -> int:
    return sum(1 for count in citations if count >= 10)


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as in_file:
        return json.load(in_file)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("site/data"))
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    summary = load_json(args.data_dir / "summary.json")
    people = load_json(args.data_dir / "people.json")
    publications_data = load_json(args.data_dir / "publications.json")
    publications = publications_data.get("publications", [])

    errors = []
    seen_dois = set()
    seen_ids = set()
    person_ids = {person["id"] for person in people}
    requires_doi = "OpenAlex" in str(summary.get("source", ""))

    for index, pub in enumerate(publications):
        label = pub.get("title") or f"record {index}"
        pub_id = pub.get("id")
        if not pub_id:
            errors.append(f"{label}: missing id")
        elif pub_id in seen_ids:
            errors.append(f"{label}: duplicate id {pub_id}")
        else:
            seen_ids.add(pub_id)

        doi = pub.get("doi")
        if requires_doi and not doi:
            errors.append(f"{label}: missing DOI")
        elif doi in seen_dois:
            errors.append(f"{label}: duplicate DOI {doi}")
        elif doi:
            seen_dois.add(doi)

        citation_count = pub.get("citation_count")
        if not isinstance(citation_count, int) or citation_count < 0:
            errors.append(f"{label}: invalid citation_count {citation_count!r}")

        year = pub.get("year")
        if year is not None and not isinstance(year, int):
            errors.append(f"{label}: invalid year {year!r}")

        for person_id in pub.get("group_author_ids", []):
            if person_id not in person_ids:
                errors.append(f"{label}: unknown group author {person_id}")

    citations = [pub.get("citation_count", 0) for pub in publications]
    publication_total_citations = summary.get("publication_total_citations", summary.get("total_citations"))
    publication_h_index = summary.get("publication_h_index", summary.get("h_index"))
    publication_i10_index = summary.get("publication_i10_index", summary.get("i10_index"))

    if summary.get("publication_count") != len(publications):
        errors.append("summary publication_count does not match publications.json")
    if publication_total_citations != sum(citations):
        errors.append("summary publication_total_citations does not match publications.json")
    if publication_h_index != h_index(citations):
        errors.append("summary publication_h_index does not match publications.json")
    if publication_i10_index is not None and publication_i10_index != i10_index(citations):
        errors.append("summary publication_i10_index does not match publications.json")

    since_year = summary.get("since_year")
    if summary.get("since_metrics_complete"):
        since_key = f"citation_count_since_{since_year}"
        since_citations = []
        for pub in publications:
            count = pub.get("citation_count_since_year", pub.get(since_key))
            if not isinstance(count, int) or count < 0:
                errors.append(f"{pub.get('title', 'publication')}: invalid since-year citation count {count!r}")
            else:
                since_citations.append(count)
        if summary.get("publication_since_citations") != sum(since_citations):
            errors.append("summary publication_since_citations does not match publications.json")
        if summary.get("since_citations") != sum(since_citations):
            errors.append("summary since_citations does not match publications.json")
        if summary.get("since_h_index") != h_index(since_citations):
            errors.append("summary since_h_index does not match publications.json")
        if summary.get("since_i10_index") != i10_index(since_citations):
            errors.append("summary since_i10_index does not match publications.json")

    max_person_h_index = max((person.get("h_index", 0) for person in people), default=0)
    if summary.get("h_index", 0) < max_person_h_index:
        errors.append("group h_index is lower than a person h_index")
    max_person_i10_index = max((person.get("i10_index", 0) for person in people), default=0)
    if summary.get("i10_index", 0) < max_person_i10_index:
        errors.append("group i10_index is lower than a person i10_index")
    max_person_citations = max((person.get("citation_count", 0) for person in people), default=0)
    if summary.get("total_citations", 0) < max_person_citations:
        errors.append("group total_citations is lower than a person citation_count")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Validated {len(publications)} publications and {len(people)} people.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
