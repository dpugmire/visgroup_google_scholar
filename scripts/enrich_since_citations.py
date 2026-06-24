#!/usr/bin/env python3
"""Fetch per-publication Google Scholar citation counts for a since-year window."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_SINCE_YEAR = 2021
DEFAULT_CACHE = Path("data/scholar/since-citations-2021.json")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)


class ScholarBlocked(RuntimeError):
    """Raised when Google Scholar returns a block or verification page."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as in_file:
        return json.load(in_file)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out_file:
        json.dump(data, out_file, indent=2, sort_keys=True)
        out_file.write("\n")


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


def count_from_record(record: dict | None) -> int | None:
    if not isinstance(record, dict):
        return None
    count = record.get("citation_count_since_year")
    if isinstance(count, int) and count >= 0:
        return count
    return None


def load_cache(path: Path, since_year: int) -> dict:
    cache = load_json(path, {})
    if not cache:
        return {"since_year": since_year, "generated_at": "", "records": {}}
    if int(cache.get("since_year") or 0) != since_year:
        raise ValueError(f"{path} is for since_year={cache.get('since_year')}, not {since_year}")
    cache.setdefault("records", {})
    return cache


def scholar_since_url(cites_url: str, since_year: int) -> str:
    parsed = urllib.parse.urlparse(cites_url or "")
    query = urllib.parse.parse_qs(parsed.query)
    if not query.get("cites"):
        return ""
    query["as_ylo"] = [str(since_year)]
    query["hl"] = ["en"]
    encoded = urllib.parse.urlencode({key: values[-1] for key, values in query.items()})
    return urllib.parse.urlunparse(parsed._replace(query=encoded))


def parse_scholar_count(page: str) -> int:
    if "Our systems have detected unusual traffic" in page or "/sorry/" in page:
        raise ScholarBlocked("Google Scholar returned an unusual-traffic page")
    if "not a robot" in page or "recaptcha" in page.lower():
        raise ScholarBlocked("Google Scholar returned a verification page")

    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text)
    if "did not match any articles" in text or "did not match any documents" in text:
        return 0

    match = re.search(r"\b(?:About\s+)?([0-9][0-9,]*)\s+results?\b", text)
    if not match:
        raise ValueError("could not find Scholar result count")
    return int(match.group(1).replace(",", ""))


def fetch_scholar_count(url: str, user_agent: str, timeout: float) -> int:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        page = response.read().decode("utf-8", "replace")
    if "/sorry/" in final_url:
        raise ScholarBlocked("Google Scholar redirected to a verification page")
    return parse_scholar_count(page)


def apply_since_counts(
    summary: dict,
    publications_data: dict,
    cache: dict,
    since_year: int,
) -> dict:
    records = cache.get("records") or {}
    publications = publications_data.get("publications", [])
    counted = 0
    inferred_zero = 0
    missing = 0
    checked_at_values = []

    for pub in publications:
        count = count_from_record(records.get(pub.get("id", "")))
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
        record = records.get(pub.get("id", ""))
        if isinstance(record, dict) and record.get("checked_at"):
            checked_at_values.append(record["checked_at"])

    since_counts = [
        int(pub["citation_count_since_year"])
        for pub in publications
        if isinstance(pub.get("citation_count_since_year"), int)
    ]
    complete = counted == len(publications)
    publication_since_citations = sum(since_counts) if complete else None

    profile_since_citations = summary.get("profile_since_citations")
    if profile_since_citations is None:
        profile_since_citations = summary.get("since_citations")

    summary.update(
        {
            "generated_at": utc_now(),
            "since_year": since_year,
            "profile_since_citations": profile_since_citations,
            "publication_since_citations": publication_since_citations,
            "since_citations": publication_since_citations if complete else profile_since_citations,
            "since_h_index": h_index(since_counts) if complete else None,
            "since_i10_index": i10_index(since_counts) if complete else None,
            "since_metrics_complete": complete,
            "since_metrics_counted_publications": counted,
            "since_metrics_missing_publications": missing,
            "since_metrics_inferred_zero_publications": inferred_zero,
            "since_metrics_checked_at": max(checked_at_values) if checked_at_values else "",
            "since_metric_source": (
                "Per-publication Google Scholar cited-by queries"
                if complete
                else "Publish or Perish profile annual totals"
            ),
        }
    )
    publications_data["generated_at"] = summary["generated_at"]
    return {
        "complete": complete,
        "counted": counted,
        "missing": missing,
        "publication_count": len(publications),
    }


def write_publications_csv(path: Path, publications: list[dict], people: list[dict]) -> None:
    people_by_id = {person["id"]: person for person in people}
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("site/data"))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--since-year", type=int, default=DEFAULT_SINCE_YEAR)
    parser.add_argument("--limit", type=int, default=0, help="maximum uncached Scholar queries; 0 means no limit")
    parser.add_argument("--sleep-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--refresh", action="store_true", help="refresh counts already present in the cache")
    parser.add_argument("--apply-only", action="store_true", help="only apply cached counts to site data")
    parser.add_argument("--dry-run", action="store_true", help="fetch and report without writing files")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    summary_path = args.data_dir / "summary.json"
    publications_path = args.data_dir / "publications.json"
    people_path = args.data_dir / "people.json"

    summary = load_json(summary_path, {})
    publications_data = load_json(publications_path, {"publications": []})
    people = load_json(people_path, [])
    cache = load_cache(args.cache, args.since_year)
    records = cache["records"]

    queried = 0
    if not args.apply_only:
        for pub in publications_data.get("publications", []):
            pub_id = pub.get("id", "")
            if not pub_id:
                continue
            if not args.refresh and count_from_record(records.get(pub_id)) is not None:
                continue
            if int(pub.get("citation_count") or 0) == 0:
                continue

            url = scholar_since_url(pub.get("google_scholar_cites_url", ""), args.since_year)
            if not url:
                continue
            if args.limit and queried >= args.limit:
                break

            try:
                count = fetch_scholar_count(url, args.user_agent, args.timeout)
            except (ScholarBlocked, ValueError, urllib.error.URLError, TimeoutError) as err:
                print(f"error: {pub.get('title', pub_id)}: {err}", file=sys.stderr)
                break

            queried += 1
            records[pub_id] = {
                "publication_id": pub_id,
                "title": pub.get("title", ""),
                "year": pub.get("year"),
                "citation_count": pub.get("citation_count", 0),
                "citation_count_since_year": count,
                "since_year": args.since_year,
                "google_scholar_cites_url": pub.get("google_scholar_cites_url", ""),
                "queried_url": url,
                "checked_at": utc_now(),
                "status": "ok",
            }
            print(f"{queried}: {count} since {args.since_year}: {pub.get('title', pub_id)}")
            if args.sleep_seconds > 0 and (not args.limit or queried < args.limit):
                time.sleep(args.sleep_seconds)

    cache["generated_at"] = utc_now()
    status = apply_since_counts(summary, publications_data, cache, args.since_year)

    if not args.dry_run:
        write_json(args.cache, cache)
        write_json(summary_path, summary)
        write_json(publications_path, publications_data)
        write_publications_csv(args.data_dir / "publications.csv", publications_data.get("publications", []), people)

    print(
        f"Since-{args.since_year} counts: {status['counted']} of "
        f"{status['publication_count']} publications populated; {status['missing']} missing."
    )
    if status["complete"]:
        print(
            f"h-index={summary['since_h_index']}, i10-index={summary['since_i10_index']}, "
            f"citations={summary['since_citations']}"
        )
    else:
        print("h-index and i10-index remain hidden until all publication counts are populated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
