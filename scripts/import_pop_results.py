#!/usr/bin/env python3
"""Import Publish or Perish Google Scholar results into the static site data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import shutil
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path


DEFAULT_POP_RESULTS_DIR = Path.home() / "Library/Application Support/Publish or Perish/Results6"
PROFILE_QUERY_TYPES = {"PoPGSProfile", "PoPGSCProfile"}
DEFAULT_SINCE_YEAR = 2021
DEFAULT_SINCE_CITATIONS_CACHE = Path("data/scholar/since-citations-2021.json")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default=None, encoding: str = "utf-8"):
    if not path.exists():
        return default
    with path.open("r", encoding=encoding) as in_file:
        return json.load(in_file)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out_file:
        json.dump(data, out_file, indent=2, sort_keys=True)
        out_file.write("\n")


def load_pop_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as in_file:
        return json.load(in_file)


def load_since_citations_cache(path: Path, since_year: int) -> dict:
    cache = load_json(path, {})
    if not cache:
        return {}
    if int(cache.get("since_year") or 0) != since_year:
        return {}
    records = cache.get("records") or {}
    return records if isinstance(records, dict) else {}


def scholar_user_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url or "")
    query = urllib.parse.parse_qs(parsed.query)
    return (query.get("user") or [""])[0]


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def title_key(title: str, year) -> str:
    return f"{normalize_text(title)}|{year or ''}"


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


def parse_annual_citations(value: str) -> list[dict]:
    entries = []
    for token in str(value or "").split(",")[1:]:
        parts = token.split(":")
        if len(parts) != 3:
            continue
        try:
            year, citations, total = [int(part) for part in parts]
        except ValueError:
            continue
        entries.append({"year": year, "citations": citations, "total": total})
    return entries


def people_by_scholar_id(people: list[dict]) -> dict[str, dict]:
    by_id = {}
    for person in people:
        user_id = scholar_user_id(person.get("google_scholar_url", ""))
        if user_id:
            by_id[user_id] = person
    return by_id


def person_id_for_result(
    result: dict,
    people: list[dict],
    people_by_scholar: dict[str, dict],
    allow_author_search: bool,
) -> tuple[str | None, str]:
    query = result.get("$query") or {}
    query_type = query.get("$type", "")

    if query_type in PROFILE_QUERY_TYPES:
        profile_id = query.get("ProfileID") or scholar_user_id(query.get("HomeURL", ""))
        person = people_by_scholar.get(profile_id)
        return (person["id"], query_type) if person else (None, query_type)

    if allow_author_search and query_type == "PoPGScholar":
        author = normalize_text(query.get("Author") or query.get("Name") or "")
        for person in people:
            if author == normalize_text(person["name"]):
                return person["id"], query_type

    return None, query_type


def collect_pop_results(
    results_dir: Path,
    people: list[dict],
    allow_author_search: bool,
) -> tuple[dict[str, dict], list[str]]:
    people_by_scholar = people_by_scholar_id(people)
    matches: dict[str, dict] = {}
    warnings = []

    for path in sorted(results_dir.glob("*.json")):
        try:
            result = load_pop_json(path)
        except (OSError, json.JSONDecodeError) as err:
            warnings.append(f"{path}: cannot read Publish or Perish JSON: {err}")
            continue

        person_id, query_type = person_id_for_result(
            result,
            people,
            people_by_scholar,
            allow_author_search,
        )
        if not person_id:
            continue

        query = result.get("$query") or {}
        previous = matches.get(person_id)
        previous_time = (previous or {}).get("$query", {}).get("QueryTime", 0)
        query_time = query.get("QueryTime", 0)
        if previous and query_time <= previous_time:
            warnings.append(
                f"{path}: ignored older {query_type} result for {person_id}; "
                "newer result already selected"
            )
            continue

        if query_type not in PROFILE_QUERY_TYPES:
            warnings.append(
                f"{path}: using {query_type} for {person_id}; Google Scholar Profile "
                "queries are preferred for matching profile metrics"
            )
        result["_source_path"] = str(path)
        matches[person_id] = result

    return matches, warnings


def normalized_corrections(raw: dict) -> dict:
    corrections = dict(raw or {})
    corrections["exclude_scholar_ids"] = set(corrections.get("exclude_scholar_ids", []))
    corrections["exclude_titles"] = {
        normalize_text(title) for title in corrections.get("exclude_titles", [])
    }
    corrections["exclude_scholar_ids_by_person"] = {
        person_id: set(ids)
        for person_id, ids in corrections.get("exclude_scholar_ids_by_person", {}).items()
    }
    corrections["exclude_titles_by_person"] = {
        person_id: {normalize_text(title) for title in titles}
        for person_id, titles in corrections.get("exclude_titles_by_person", {}).items()
    }
    return corrections


def row_is_excluded(row: dict, person_id: str, corrections: dict) -> bool:
    uid = row.get("uid") or ""
    normalized_title = normalize_text(row.get("title") or "")
    if uid and uid in corrections["exclude_scholar_ids"]:
        return True
    if normalized_title in corrections["exclude_titles"]:
        return True
    if uid and uid in corrections["exclude_scholar_ids_by_person"].get(person_id, set()):
        return True
    return normalized_title in corrections["exclude_titles_by_person"].get(person_id, set())


def row_to_publication(row: dict, person_id: str) -> dict:
    authors = row.get("authors") or []
    if isinstance(authors, str):
        author_names = [part.strip() for part in authors.split(",") if part.strip()]
    else:
        author_names = [str(author).strip() for author in authors if str(author).strip()]

    uid = row.get("uid") or ""
    title = html.unescape(row.get("title") or "(untitled)")
    year = row.get("year") or None
    citations = int(row.get("cites") or 0)

    return {
        "id": f"scholar:{uid}" if uid else f"scholar-title:{title_key(title, year)}",
        "scholar_uid": uid,
        "scholar_uids": [uid] if uid else [],
        "doi": row.get("doi") or "",
        "doi_url": f"https://doi.org/{row.get('doi')}" if row.get("doi") else "",
        "title": title,
        "year": year,
        "publication_date": "",
        "type": row.get("type") or "",
        "venue": html.unescape(row.get("source") or ""),
        "publisher": html.unescape(row.get("publisher") or ""),
        "citation_count": citations,
        "authors": [{"name": name, "openalex_id": ""} for name in author_names],
        "author_names": author_names,
        "openalex_id": "",
        "openalex_ids": [],
        "openalex_url": "",
        "url": row.get("article_url") or row.get("fulltext_url") or "",
        "oa_url": row.get("fulltext_url") or "",
        "google_scholar_cites_url": row.get("cites_url") or "",
        "biblio": {
            "volume": row.get("volume") or "",
            "issue": row.get("issue") or "",
            "first_page": row.get("startpage") or "",
            "last_page": row.get("endpage") or "",
        },
        "group_author_ids": [person_id],
        "source_person_ids": [person_id],
        "source": "Google Scholar via Publish or Perish",
    }


def merge_publication(existing: dict, incoming: dict) -> dict:
    existing["group_author_ids"] = sorted(
        set(existing.get("group_author_ids", [])) | set(incoming.get("group_author_ids", []))
    )
    existing["source_person_ids"] = sorted(
        set(existing.get("source_person_ids", [])) | set(incoming.get("source_person_ids", []))
    )
    existing["scholar_uids"] = sorted(
        set(existing.get("scholar_uids", [])) | set(incoming.get("scholar_uids", []))
    )
    if incoming.get("citation_count", 0) > existing.get("citation_count", 0):
        existing["citation_count"] = incoming["citation_count"]
        existing["google_scholar_cites_url"] = incoming.get("google_scholar_cites_url", "")
    if len(incoming.get("author_names", [])) > len(existing.get("author_names", [])):
        existing["authors"] = incoming.get("authors", [])
        existing["author_names"] = incoming.get("author_names", [])

    for key in ["doi", "doi_url", "venue", "publisher", "type", "url", "oa_url"]:
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]

    return existing


def apply_since_citation_counts(publications: list[dict], records: dict[str, dict], since_year: int) -> dict:
    counted = 0
    inferred_zero = 0
    missing = 0
    checked_at_values = []

    for pub in publications:
        record = records.get(pub.get("id", ""))
        count = None
        if isinstance(record, dict):
            raw_count = record.get("citation_count_since_year")
            if isinstance(raw_count, int) and raw_count >= 0:
                count = raw_count
                if record.get("checked_at"):
                    checked_at_values.append(record["checked_at"])

        if count is None and int(pub.get("citation_count") or 0) == 0:
            count = 0
            inferred_zero += 1

        if count is None:
            pub.pop("citation_count_since_year", None)
            pub.pop(f"citation_count_since_{since_year}", None)
            missing += 1
            continue

        pub["citation_count_since_year"] = count
        pub[f"citation_count_since_{since_year}"] = count
        counted += 1

    since_counts = [
        int(pub["citation_count_since_year"])
        for pub in publications
        if isinstance(pub.get("citation_count_since_year"), int)
    ]
    complete = counted == len(publications)
    return {
        "complete": complete,
        "counted_publications": counted,
        "missing_publications": missing,
        "inferred_zero_publications": inferred_zero,
        "checked_at": max(checked_at_values) if checked_at_values else "",
        "publication_since_citations": sum(since_counts) if complete else None,
        "since_h_index": h_index(since_counts) if complete else None,
        "since_i10_index": i10_index(since_counts) if complete else None,
    }


def build_outputs(
    people: list[dict],
    matches: dict[str, dict],
    corrections: dict,
    since_citation_records: dict[str, dict],
    since_year: int,
) -> tuple[dict, list[dict], list[dict]]:
    publications_by_key: dict[str, dict] = {}
    people_output = []
    warnings = []

    for person in people:
        result = matches.get(person["id"])
        item = dict(person)
        item["openalex_url"] = ""
        item["resolved_openalex_id"] = ""
        item["scholar_user_id"] = scholar_user_id(person.get("google_scholar_url", ""))
        item["publish_or_perish_query_type"] = ""
        item["works_count"] = 0
        item["citation_count"] = 0
        item["h_index"] = 0
        item["i10_index"] = 0

        if not result:
            if item["scholar_user_id"]:
                warnings.append(f"missing Publish or Perish Google Scholar Profile result for {person['id']}")
            else:
                warnings.append(f"{person['id']} has no Google Scholar profile configured")
            people_output.append(item)
            continue

        query = result.get("$query") or {}
        rows = []
        seen_person_keys = set()
        for row in result.get("$results") or []:
            if row_is_excluded(row, person["id"], corrections):
                continue
            key = title_key(row.get("title") or "", row.get("year") or "")
            if not key or key in seen_person_keys:
                continue
            seen_person_keys.add(key)
            rows.append(row)

            publication = row_to_publication(row, person["id"])
            if key in publications_by_key:
                publications_by_key[key] = merge_publication(publications_by_key[key], publication)
            else:
                publications_by_key[key] = publication

        citations = [int(row.get("cites") or 0) for row in rows]
        metrics = result.get("$metrics") or {}
        item["works_count"] = int(metrics.get("papers_total") or len(rows))
        item["citation_count"] = int(metrics.get("cites_total") or sum(citations))
        item["h_index"] = int(metrics.get("h") or h_index(citations))
        item["i10_index"] = i10_index(citations)
        item["publish_or_perish_query_type"] = query.get("$type", "")
        item["publish_or_perish_query_time"] = query.get("QueryTime", 0)
        item["annual_citations"] = parse_annual_citations(query.get("AnnualCites", ""))
        people_output.append(item)

    publications = sorted(
        publications_by_key.values(),
        key=lambda pub: (pub.get("year") or 0, pub.get("citation_count") or 0, pub.get("title") or ""),
        reverse=True,
    )
    citations = [int(pub.get("citation_count", 0)) for pub in publications]
    publication_total_citations = sum(citations)
    publication_h_index = h_index(citations)
    publication_i10_index = i10_index(citations)
    max_person_citations = max((int(person.get("citation_count") or 0) for person in people_output), default=0)
    max_person_h_index = max((int(person.get("h_index") or 0) for person in people_output), default=0)
    max_person_i10_index = max((int(person.get("i10_index") or 0) for person in people_output), default=0)
    total_citations = max(publication_total_citations, max_person_citations)
    group_h_index = max(publication_h_index, max_person_h_index)
    group_i10_index = max(publication_i10_index, max_person_i10_index)

    if group_h_index != publication_h_index:
        warnings.append(
            "group h_index was raised to the highest member h_index because the "
            "imported publication rows compute a lower value"
        )
    if group_i10_index != publication_i10_index:
        warnings.append(
            "group i10_index was raised to the highest member i10_index because the "
            "imported publication rows compute a lower value"
        )
    if total_citations != publication_total_citations:
        warnings.append(
            "group total_citations was raised to the highest member citation total because "
            "the imported publication rows compute a lower value"
        )

    annual_by_year = defaultdict(lambda: {"citations": 0, "total": 0})
    for person in people_output:
        for entry in person.get("annual_citations", []):
            year = int(entry.get("year") or 0)
            annual_by_year[year]["citations"] += int(entry.get("citations") or 0)
            annual_by_year[year]["total"] += int(entry.get("total") or 0)
    profile_annual_citations = [
        {"year": year, **annual_by_year[year]}
        for year in sorted(annual_by_year)
        if year
    ]
    profile_since_citations = sum(
        entry["citations"]
        for entry in profile_annual_citations
        if entry["year"] >= since_year
    )
    since_metrics = apply_since_citation_counts(publications, since_citation_records, since_year)
    since_citations = (
        since_metrics["publication_since_citations"]
        if since_metrics["complete"]
        else profile_since_citations
    )
    since_metric_source = (
        "Per-publication Google Scholar cited-by queries"
        if since_metrics["complete"]
        else "Publish or Perish profile annual totals"
    )

    summary = {
        "generated_at": utc_now(),
        "source": "Google Scholar via Publish or Perish",
        "member_count": len(people),
        "publication_count": len(publications),
        "total_citations": total_citations,
        "h_index": group_h_index,
        "i10_index": group_i10_index,
        "publication_total_citations": publication_total_citations,
        "publication_h_index": publication_h_index,
        "publication_i10_index": publication_i10_index,
        "profile_annual_citations": profile_annual_citations,
        "since_year": since_year,
        "since_citations": since_citations,
        "profile_since_citations": profile_since_citations,
        "publication_since_citations": since_metrics["publication_since_citations"],
        "since_h_index": since_metrics["since_h_index"],
        "since_i10_index": since_metrics["since_i10_index"],
        "since_metrics_complete": since_metrics["complete"],
        "since_metrics_counted_publications": since_metrics["counted_publications"],
        "since_metrics_missing_publications": since_metrics["missing_publications"],
        "since_metrics_inferred_zero_publications": since_metrics["inferred_zero_publications"],
        "since_metrics_checked_at": since_metrics["checked_at"],
        "since_metric_source": since_metric_source,
        "person_metric_source": "Publish or Perish profile metrics when available",
        "warnings": warnings,
    }
    return summary, people_output, publications


def write_publications_csv(path: Path, publications: list[dict], people_by_id: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as out_file:
        writer = csv.DictWriter(
            out_file,
            fieldnames=[
                "title",
                "year",
                "citation_count",
                "citation_count_since_year",
                "scholar_uids",
                "venue",
                "group_authors",
                "url",
                "google_scholar_cites_url",
            ],
        )
        writer.writeheader()
        for pub in publications:
            group_authors = [
                people_by_id[person_id]["name"]
                for person_id in pub.get("group_author_ids", [])
                if person_id in people_by_id
            ]
            writer.writerow(
                {
                    "title": pub.get("title", ""),
                    "year": pub.get("year", ""),
                    "citation_count": pub.get("citation_count", 0),
                    "citation_count_since_year": pub.get("citation_count_since_year", ""),
                    "scholar_uids": "; ".join(pub.get("scholar_uids", [])),
                    "venue": pub.get("venue", ""),
                    "group_authors": "; ".join(group_authors),
                    "url": pub.get("url", ""),
                    "google_scholar_cites_url": pub.get("google_scholar_cites_url", ""),
                }
            )


def copy_raw_results(matches: dict[str, dict], raw_out_dir: Path) -> None:
    raw_out_dir.mkdir(parents=True, exist_ok=True)
    for person_id, result in matches.items():
        source_path = Path(result["_source_path"])
        destination_path = raw_out_dir / f"{person_id}.json"
        if source_path.resolve() == destination_path.resolve():
            continue
        shutil.copy2(source_path, destination_path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--people", type=Path, default=Path("data/people.json"))
    parser.add_argument("--corrections", type=Path, default=Path("data/corrections.json"))
    parser.add_argument("--pop-results-dir", type=Path, default=DEFAULT_POP_RESULTS_DIR)
    parser.add_argument("--raw-out-dir", type=Path, default=Path("data/scholar/raw"))
    parser.add_argument("--out-dir", type=Path, default=Path("site/data"))
    parser.add_argument("--since-year", type=int, default=DEFAULT_SINCE_YEAR)
    parser.add_argument("--since-citations-cache", type=Path, default=DEFAULT_SINCE_CITATIONS_CACHE)
    parser.add_argument(
        "--allow-author-search",
        action="store_true",
        help="accept broad Google Scholar author-name search results when no profile result exists",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    people = load_json(args.people, [])
    corrections = normalized_corrections(load_json(args.corrections, {}))
    since_citation_records = load_since_citations_cache(args.since_citations_cache, args.since_year)

    matches, collection_warnings = collect_pop_results(
        args.pop_results_dir,
        people,
        args.allow_author_search,
    )
    missing = [
        person["id"]
        for person in people
        if scholar_user_id(person.get("google_scholar_url", "")) and person["id"] not in matches
    ]
    if missing:
        print(
            "Missing Publish or Perish Google Scholar Profile results for: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        if not args.allow_author_search:
            print(
                "Run Google Scholar Profile queries in Publish or Perish, then rerun this importer. "
                "Use --allow-author-search only for temporary broad-name search imports.",
                file=sys.stderr,
            )
            return 1

    copy_raw_results(matches, args.raw_out_dir)
    summary, people_output, publications = build_outputs(
        people,
        matches,
        corrections,
        since_citation_records,
        args.since_year,
    )
    summary["warnings"].extend(collection_warnings)
    people_by_id = {person["id"]: person for person in people_output}

    write_json(args.out_dir / "summary.json", summary)
    write_json(args.out_dir / "people.json", people_output)
    write_json(
        args.out_dir / "publications.json",
        {
            "generated_at": summary["generated_at"],
            "source": summary["source"],
            "publications": publications,
        },
    )
    write_publications_csv(args.out_dir / "publications.csv", publications, people_by_id)

    print(
        f"Imported {len(matches)} Publish or Perish result files and wrote "
        f"{len(publications)} deduplicated Scholar publications to {args.out_dir}"
    )
    for warning in summary.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
