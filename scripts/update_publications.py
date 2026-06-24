#!/usr/bin/env python3
"""Update static publication data for the visualization group site."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


OPENALEX_BASE_URL = "https://api.openalex.org"
ORCID_BASE_URL = "https://pub.orcid.org/v3.0"
DEFAULT_SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "cited_by_count",
        "authorships",
        "primary_location",
        "open_access",
        "biblio",
        "ids",
    ]
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as in_file:
        return json.load(in_file)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out_file:
        json.dump(data, out_file, indent=2, sort_keys=True)
        out_file.write("\n")


def normalize_doi(value) -> str:
    if not value:
        return ""
    doi = str(value).strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.strip().strip(".")
    return doi.lower()


def compact_openalex_id(value: str) -> str:
    if not value:
        return ""
    return value.rstrip("/").split("/")[-1]


def openalex_author_url(openalex_id: str) -> str:
    if not openalex_id:
        return ""
    if openalex_id.startswith("http"):
        return openalex_id
    return f"https://openalex.org/{compact_openalex_id(openalex_id)}"


def request_url_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = 2,
):
    params = dict(params or {})

    if params:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"

    request_headers = {"User-Agent": "visualization-group-scholar/0.1"}
    request_headers.update(headers or {})
    last_error = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {err.code} for {url}: {body}")
            if err.code not in {429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError) as err:
            last_error = RuntimeError(f"request failed for {url}: {err}")

        if attempt < retries:
            time.sleep(2**attempt)

    raise last_error


def request_json(url_or_path: str, params: dict | None = None, retries: int = 2):
    params = dict(params or {})
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    if api_key:
        params.setdefault("api_key", api_key)
    if mailto:
        params.setdefault("mailto", mailto)

    if url_or_path.startswith("http"):
        url = url_or_path
    else:
        url = f"{OPENALEX_BASE_URL}{url_or_path}"

    return request_url_json(url, params=params, retries=retries)


def resolve_author(person: dict) -> tuple[dict | None, str | None]:
    openalex_id = person.get("openalex_id", "").strip()
    if openalex_id:
        author_path = f"/authors/{compact_openalex_id(openalex_id)}"
        try:
            return request_json(author_path), None
        except RuntimeError as err:
            return None, str(err)

    orcid = person.get("orcid", "").strip()
    if not orcid:
        return None, "missing ORCID or OpenAlex author ID"

    full_orcid = f"https://orcid.org/{orcid}"
    candidates = [
        f"/authors/{urllib.parse.quote(full_orcid, safe='')}",
        f"/authors/{full_orcid}",
    ]
    errors = []
    for candidate in candidates:
        try:
            return request_json(candidate), None
        except RuntimeError as err:
            errors.append(str(err))

    try:
        data = request_json("/authors", {"filter": f"orcid:{full_orcid}", "per_page": 5})
        results = data.get("results", [])
        if results:
            return results[0], None
    except RuntimeError as err:
        errors.append(str(err))

    return None, "; ".join(errors)


def fetch_author_works(author_id: str, max_pages: int | None = None) -> list[dict]:
    short_id = compact_openalex_id(author_id)
    if not short_id:
        return []

    works = []
    cursor = "*"
    page_count = 0
    while cursor:
        params = {
            "filter": f"authorships.author.id:{short_id}",
            "per_page": 100,
            "cursor": cursor,
            "select": DEFAULT_SELECT_FIELDS,
        }
        data = request_json("/works", params)
        works.extend(data.get("results", []))

        page_count += 1
        if max_pages is not None and page_count >= max_pages:
            break

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(0.12)

    return works


def nested_value(data: dict | None, *keys: str) -> str:
    current = data or {}
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key) or {}
    if isinstance(current, str):
        return current
    return ""


def parse_orcid_year(summary: dict) -> int | None:
    value = nested_value(summary, "publication-date", "year", "value")
    try:
        return int(value) if value else None
    except ValueError:
        return None


def orcid_group_metadata(group: dict) -> dict:
    summaries = group.get("work-summary") or []
    summary = summaries[0] if summaries else {}
    return {
        "title": nested_value(summary, "title", "title", "value"),
        "year": parse_orcid_year(summary),
        "venue": nested_value(summary, "journal-title", "value"),
        "type": summary.get("type") or "",
        "url": nested_value(summary, "url", "value"),
    }


def fetch_orcid_works(orcid: str) -> list[dict]:
    url = f"{ORCID_BASE_URL}/{orcid}/works"
    data = request_url_json(url, headers={"Accept": "application/json"})
    works = []
    seen = set()

    for group in data.get("group") or []:
        metadata = orcid_group_metadata(group)
        external_ids = (group.get("external-ids") or {}).get("external-id") or []
        for external_id in external_ids:
            external_type = (external_id.get("external-id-type") or "").lower()
            if external_type != "doi":
                continue
            doi = normalize_doi(
                external_id.get("external-id-value")
                or nested_value(external_id, "external-id-url", "value")
            )
            if not doi or doi in seen:
                continue
            seen.add(doi)
            item = dict(metadata)
            item["doi"] = doi
            works.append(item)

    return works


def fetch_work_by_doi(doi: str) -> dict | None:
    full_doi_url = f"https://doi.org/{doi}"
    candidates = [
        f"/works/{urllib.parse.quote(full_doi_url, safe='')}",
        f"/works/{full_doi_url}",
    ]
    errors = []
    for candidate in candidates:
        try:
            return request_json(candidate)
        except RuntimeError as err:
            errors.append(str(err))

    try:
        data = request_json("/works", {"filter": f"doi:{doi}", "per_page": 1})
        results = data.get("results", [])
        return results[0] if results else None
    except RuntimeError as err:
        errors.append(str(err))

    return None


def get_venue(work: dict) -> str:
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return source.get("display_name") or ""


def get_landing_url(work: dict, doi: str) -> str:
    if doi:
        return f"https://doi.org/{doi}"
    location = work.get("primary_location") or {}
    return location.get("landing_page_url") or work.get("id") or ""


def authors_from_work(work: dict) -> list[dict]:
    authors = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(
                {
                    "name": name,
                    "openalex_id": author.get("id") or "",
                }
            )
    return authors


def publication_from_work(work: dict) -> dict | None:
    doi = normalize_doi(work.get("doi") or (work.get("ids") or {}).get("doi"))
    if not doi:
        return None

    authors = authors_from_work(work)
    open_access = work.get("open_access") or {}
    biblio = work.get("biblio") or {}
    cited_by_count = work.get("cited_by_count") or 0

    return {
        "id": f"doi:{doi}",
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}",
        "title": work.get("display_name") or work.get("title") or "(untitled)",
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date") or "",
        "type": work.get("type") or "",
        "venue": get_venue(work),
        "citation_count": int(cited_by_count),
        "authors": authors,
        "author_names": [author["name"] for author in authors],
        "openalex_id": work.get("id") or "",
        "openalex_ids": [work.get("id")] if work.get("id") else [],
        "openalex_url": work.get("id") or "",
        "url": get_landing_url(work, doi),
        "oa_url": open_access.get("oa_url") or "",
        "biblio": {
            "volume": biblio.get("volume") or "",
            "issue": biblio.get("issue") or "",
            "first_page": biblio.get("first_page") or "",
            "last_page": biblio.get("last_page") or "",
        },
        "group_author_ids": [],
        "orcid_person_ids": [],
        "openalex_author_person_ids": [],
        "review_status": "",
        "review_notes": [],
    }


def publication_from_orcid_work(work: dict) -> dict:
    doi = normalize_doi(work.get("doi"))
    return {
        "id": f"doi:{doi}",
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}",
        "title": work.get("title") or doi,
        "year": work.get("year"),
        "publication_date": "",
        "type": work.get("type") or "",
        "venue": work.get("venue") or "",
        "citation_count": 0,
        "authors": [],
        "author_names": [],
        "openalex_id": "",
        "openalex_ids": [],
        "openalex_url": "",
        "url": f"https://doi.org/{doi}",
        "oa_url": "",
        "biblio": {
            "volume": "",
            "issue": "",
            "first_page": "",
            "last_page": "",
        },
        "group_author_ids": [],
        "orcid_person_ids": [],
        "openalex_author_person_ids": [],
        "review_status": "",
        "review_notes": [],
    }


def merge_publication(existing: dict, incoming: dict) -> dict:
    existing["group_author_ids"] = sorted(
        set(existing.get("group_author_ids", [])) | set(incoming.get("group_author_ids", []))
    )
    existing["orcid_person_ids"] = sorted(
        set(existing.get("orcid_person_ids", [])) | set(incoming.get("orcid_person_ids", []))
    )
    existing["openalex_author_person_ids"] = sorted(
        set(existing.get("openalex_author_person_ids", []))
        | set(incoming.get("openalex_author_person_ids", []))
    )
    existing["openalex_ids"] = sorted(
        set(existing.get("openalex_ids", [])) | set(incoming.get("openalex_ids", []))
    )
    if incoming.get("citation_count", 0) > existing.get("citation_count", 0):
        existing["citation_count"] = incoming["citation_count"]
    if len(incoming.get("author_names", [])) > len(existing.get("author_names", [])):
        existing["authors"] = incoming.get("authors", [])
        existing["author_names"] = incoming.get("author_names", [])

    for key in ["title", "venue", "publication_date", "type", "url", "oa_url", "openalex_id", "openalex_url"]:
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]

    if not existing.get("year") and incoming.get("year"):
        existing["year"] = incoming["year"]

    return existing


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


def normalize_corrections(raw: dict) -> dict:
    corrections = dict(raw or {})
    corrections["exclude_dois"] = {normalize_doi(doi) for doi in corrections.get("exclude_dois", [])}
    corrections["exclude_dois_by_person"] = {
        person_id: {normalize_doi(doi) for doi in dois}
        for person_id, dois in corrections.get("exclude_dois_by_person", {}).items()
    }
    corrections["doi_aliases"] = {
        normalize_doi(alias): normalize_doi(canonical)
        for alias, canonical in corrections.get("doi_aliases", {}).items()
    }
    return corrections


def remove_person_from_publication(publication: dict, person_id: str) -> None:
    for key in ["group_author_ids", "orcid_person_ids", "openalex_author_person_ids"]:
        publication[key] = sorted(set(publication.get(key, [])) - {person_id})


def apply_publication_corrections(publications: dict[str, dict], corrections: dict) -> dict[str, dict]:
    for doi in list(publications):
        if doi in corrections.get("exclude_dois", set()):
            del publications[doi]

    for doi, override in corrections.get("overrides", {}).items():
        normalized = normalize_doi(doi)
        if normalized in publications:
            publications[normalized].update(override)

    for doi, group_authors in corrections.get("force_group_authors", {}).items():
        normalized = normalize_doi(doi)
        if normalized in publications:
            publications[normalized]["group_author_ids"] = sorted(
                set(publications[normalized].get("group_author_ids", [])) | set(group_authors)
            )

    for doi, group_authors in corrections.get("remove_group_authors", {}).items():
        normalized = normalize_doi(doi)
        if normalized in publications:
            publications[normalized]["group_author_ids"] = sorted(
                set(publications[normalized].get("group_author_ids", [])) - set(group_authors)
            )

    for person_id, dois in corrections.get("exclude_dois_by_person", {}).items():
        for doi in dois:
            if doi in publications:
                remove_person_from_publication(publications[doi], person_id)

    for doi in list(publications):
        if not publications[doi].get("group_author_ids"):
            del publications[doi]

    return publications


def set_review_status(publication: dict) -> None:
    if publication.get("orcid_person_ids"):
        publication["review_status"] = "trusted_orcid"
        publication["review_notes"] = []
        return

    publication["review_status"] = "candidate_openalex"
    publication["review_notes"] = [
        "Included from OpenAlex author-profile data without ORCID DOI evidence; review recommended."
    ]


def enrich_people(people: list[dict], publications: list[dict], resolved_authors: dict[str, dict]) -> list[dict]:
    enriched = []
    for person in people:
        person_id = person["id"]
        person_pubs = [pub for pub in publications if person_id in pub.get("group_author_ids", [])]
        citations = [int(pub.get("citation_count", 0)) for pub in person_pubs]
        resolved = resolved_authors.get(person_id) or {}
        explicit_openalex_id = person.get("openalex_id", "")
        item = dict(person)
        item["openalex_id"] = explicit_openalex_id
        item["openalex_url"] = openalex_author_url(explicit_openalex_id)
        item["resolved_openalex_id"] = resolved.get("id") or ""
        item["works_count"] = len(person_pubs)
        item["citation_count"] = sum(citations)
        item["h_index"] = h_index(citations)
        item["i10_index"] = i10_index(citations)
        enriched.append(item)
    return enriched


def add_publication(
    publications_by_doi: dict[str, dict],
    publication: dict,
    person_id: str,
    author_to_person: dict[str, str],
    corrections: dict,
    source_type: str,
) -> None:
    doi_aliases = corrections["doi_aliases"]
    exclude_dois = corrections["exclude_dois"]

    doi = doi_aliases.get(publication["doi"], publication["doi"])
    if doi in exclude_dois:
        return

    publication["doi"] = doi
    publication["id"] = f"doi:{doi}"
    publication["doi_url"] = f"https://doi.org/{doi}"
    publication.setdefault("group_author_ids", [])
    publication.setdefault("orcid_person_ids", [])
    publication.setdefault("openalex_author_person_ids", [])
    publication["group_author_ids"].append(person_id)
    if source_type == "orcid":
        publication["orcid_person_ids"].append(person_id)
    elif source_type == "openalex_author":
        publication["openalex_author_person_ids"].append(person_id)

    for author_record in publication.get("authors", []):
        author_id = author_record.get("openalex_id") or ""
        matched_person_id = author_to_person.get(author_id) or author_to_person.get(compact_openalex_id(author_id))
        if matched_person_id:
            publication["group_author_ids"].append(matched_person_id)

    publication["group_author_ids"] = sorted(set(publication["group_author_ids"]))

    if doi in publications_by_doi:
        publications_by_doi[doi] = merge_publication(publications_by_doi[doi], publication)
    else:
        publications_by_doi[doi] = publication


def build_outputs(
    people: list[dict],
    corrections: dict,
    max_pages: int | None,
    author_source: str,
) -> tuple[dict, list[dict], list[dict]]:
    warnings = []
    resolved_authors: dict[str, dict] = {}
    author_to_person: dict[str, str] = {}

    for person in people:
        author, error = resolve_author(person)
        if not author:
            warnings.append({"person_id": person["id"], "message": error or "author resolution failed"})
            continue
        resolved_authors[person["id"]] = author
        author_id = author.get("id") or ""
        if author_id:
            author_to_person[author_id] = person["id"]
            author_to_person[compact_openalex_id(author_id)] = person["id"]

    publications_by_doi: dict[str, dict] = {}
    openalex_work_cache: dict[str, dict | None] = {}

    for person in people:
        used_orcid = False
        use_orcid = author_source in {"curated-openalex", "orcid-first", "orcid"}
        use_openalex = author_source in {"curated-openalex", "openalex-authors"}

        if use_orcid and person.get("orcid"):
            try:
                orcid_works = fetch_orcid_works(person["orcid"])
                used_orcid = bool(orcid_works)
                for orcid_work in orcid_works:
                    doi = corrections["doi_aliases"].get(orcid_work["doi"], orcid_work["doi"])
                    if doi not in openalex_work_cache:
                        openalex_work_cache[doi] = fetch_work_by_doi(doi)
                        time.sleep(0.12)
                    openalex_work = openalex_work_cache[doi]
                    publication = (
                        publication_from_work(openalex_work)
                        if openalex_work
                        else publication_from_orcid_work(orcid_work)
                    )
                    if not publication:
                        publication = publication_from_orcid_work(orcid_work)
                    add_publication(
                        publications_by_doi,
                        publication,
                        person["id"],
                        author_to_person,
                        corrections,
                        "orcid",
                    )
            except RuntimeError as err:
                warnings.append({"person_id": person["id"], "message": f"ORCID fetch failed: {err}"})

        if used_orcid or author_source == "orcid":
            if author_source != "curated-openalex":
                continue

        author = resolved_authors.get(person["id"])
        if not author or not use_openalex and author_source != "orcid-first":
            continue
        for work in fetch_author_works(author.get("id", ""), max_pages=max_pages):
            publication = publication_from_work(work)
            if publication:
                add_publication(
                    publications_by_doi,
                    publication,
                    person["id"],
                    author_to_person,
                    corrections,
                    "openalex_author",
                )

    publications_by_doi = apply_publication_corrections(publications_by_doi, corrections)
    for publication in publications_by_doi.values():
        set_review_status(publication)
    publications = sorted(
        publications_by_doi.values(),
        key=lambda pub: (pub.get("year") or 0, pub.get("citation_count") or 0, pub.get("title") or ""),
        reverse=True,
    )

    people_output = enrich_people(people, publications, resolved_authors)
    citations = [int(pub.get("citation_count", 0)) for pub in publications]
    source_labels = {
        "curated-openalex": "Curated OpenAlex author candidates with ORCID DOI evidence",
        "orcid-first": "ORCID DOI lists enriched by OpenAlex; fallback to OpenAlex author profiles",
        "orcid": "ORCID DOI lists enriched by OpenAlex",
        "openalex-authors": "OpenAlex author profiles",
    }
    review_status_counts = {
        "trusted_orcid": sum(1 for pub in publications if pub.get("review_status") == "trusted_orcid"),
        "candidate_openalex": sum(1 for pub in publications if pub.get("review_status") == "candidate_openalex"),
    }
    summary = {
        "generated_at": utc_now(),
        "source": source_labels[author_source],
        "member_count": len(people),
        "publication_count": len(publications),
        "total_citations": sum(citations),
        "h_index": h_index(citations),
        "i10_index": i10_index(citations),
        "review_status_counts": review_status_counts,
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
                "doi",
                "venue",
                "group_authors",
                "url",
                "openalex_url",
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
                    "doi": pub.get("doi", ""),
                    "venue": pub.get("venue", ""),
                    "group_authors": "; ".join(group_authors),
                    "url": pub.get("url", ""),
                    "openalex_url": pub.get("openalex_url", ""),
                }
            )


def person_names(person_ids: list[str], people_by_id: dict[str, dict]) -> str:
    return "; ".join(
        people_by_id[person_id]["name"]
        for person_id in person_ids
        if person_id in people_by_id
    )


def write_review_outputs(review_dir: Path, publications: list[dict], people_by_id: dict[str, dict]) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    review_rows = []
    for pub in publications:
        review_rows.append(
            {
                "review_status": pub.get("review_status", ""),
                "doi": pub.get("doi", ""),
                "title": pub.get("title", ""),
                "year": pub.get("year", ""),
                "citation_count": pub.get("citation_count", 0),
                "venue": pub.get("venue", ""),
                "group_authors": person_names(pub.get("group_author_ids", []), people_by_id),
                "orcid_evidence": person_names(pub.get("orcid_person_ids", []), people_by_id),
                "openalex_candidate_for": person_names(
                    pub.get("openalex_author_person_ids", []), people_by_id
                ),
                "review_notes": " ".join(pub.get("review_notes", [])),
                "url": pub.get("url", ""),
                "openalex_url": pub.get("openalex_url", ""),
            }
        )

    write_json(
        review_dir / "publication_candidates.json",
        {
            "generated_at": utc_now(),
            "publications": review_rows,
        },
    )

    with (review_dir / "publication_candidates.csv").open("w", encoding="utf-8", newline="") as out_file:
        fieldnames = [
            "review_status",
            "doi",
            "title",
            "year",
            "citation_count",
            "venue",
            "group_authors",
            "orcid_evidence",
            "openalex_candidate_for",
            "review_notes",
            "url",
            "openalex_url",
        ]
        writer = csv.DictWriter(out_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--people", type=Path, default=Path("data/people.json"))
    parser.add_argument("--corrections", type=Path, default=Path("data/corrections.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("site/data"))
    parser.add_argument("--review-dir", type=Path, default=Path("data/review"))
    parser.add_argument("--max-pages", type=int, default=None, help="limit OpenAlex pages per author")
    parser.add_argument(
        "--author-source",
        choices=["curated-openalex", "orcid-first", "orcid", "openalex-authors"],
        default="curated-openalex",
        help="publication source: curated OpenAlex candidates with ORCID evidence is the default",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    people = load_json(args.people, [])
    corrections = normalize_corrections(load_json(args.corrections, {}))

    summary, people_output, publications = build_outputs(
        people,
        corrections,
        args.max_pages,
        args.author_source,
    )
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
    write_review_outputs(args.review_dir, publications, people_by_id)

    print(
        f"Wrote {len(publications)} unique DOI publications for {len(people_output)} people "
        f"to {args.out_dir}"
    )
    for warning in summary.get("warnings", []):
        print(f"warning: {warning['person_id']}: {warning['message']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
