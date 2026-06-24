# Visualization Group Scholar

Static GitHub Pages site for group-level publications and citation metrics.

The site is intentionally generated from committed data files. A weekly GitHub
Actions workflow gets broad DOI-bearing candidate works from OpenAlex author
profiles, records which DOIs are also confirmed by ORCID records, deduplicates
papers across group members by DOI, computes group and per-person metrics, and
commits the generated data back to the repository.

## Structure

- `data/people.json`: current group members and stable external profile IDs.
- `data/corrections.json`: manual exclusions, DOI aliases, and metadata fixes.
- `data/review/publication_candidates.csv`: generated curation queue.
- `scripts/update_publications.py`: fetches ORCID/OpenAlex data and generates site data.
- `scripts/export_bibtex.py`: writes `site/data/publications.bib` from generated publications.
- `scripts/validate_data.py`: checks generated data for duplicate or malformed records.
- `site/`: static GitHub Pages site.
- `.github/workflows/update-data.yml`: weekly data update and commit.
- `.github/workflows/deploy-pages.yml`: deploys `site/` to GitHub Pages.

## Local Update

Run the updater from the repository root:

```bash
python3 scripts/update_publications.py
python3 scripts/validate_data.py
```

To use Google Scholar data exported by Publish or Perish instead:

```bash
python3 scripts/import_pop_results.py
python3 scripts/validate_data.py
```

The importer reads Publish or Perish JSON results from:

```text
~/Library/Application Support/Publish or Perish/Results6/
```

It expects Google Scholar Profile queries matching the `user=` IDs in
`data/people.json`. Broad author-name searches can be imported with
`--allow-author-search`, but profile queries are preferred because they match the
curated Google Scholar profile metrics.

Members without a Google Scholar profile can still be listed in
`data/people.json`; they are included in the member list with zero
Google-Scholar-derived publication metrics until another data source is added for
them.

If Publish or Perish reuses one live result file while refreshing multiple
profiles, save one raw JSON snapshot per person under `data/scholar/raw/`, then
regenerate from those saved snapshots with:

```bash
python3 scripts/import_pop_results.py --pop-results-dir data/scholar/raw --raw-out-dir data/scholar/raw
python3 scripts/export_bibtex.py
python3 scripts/validate_data.py
```

Publish or Perish stores profile-level totals and h-index values separately from
the publication rows. The generated summary keeps publication-row metrics for
auditing and reports group metrics as at least the highest member metric, which
preserves the expected group h-index invariant when a saved result is incomplete
or manually filtered.

Publish or Perish profile snapshots include annual citation totals, so the site
can show a `Since 2021` citation count from those profile annual totals. They do
not include per-paper citation counts by year, which are required for a
Google-Scholar-style `Since 2021` h-index and i10-index.

To populate those metrics, enrich the deduplicated publication list with
per-publication cited-by counts:

```bash
python3 scripts/enrich_since_citations.py --sleep-seconds 10
python3 scripts/validate_data.py
```

The enrichment script queries each publication's Google Scholar `Cited by` page
with a lower year bound, writes resumable counts to
`data/scholar/since-citations-2021.json`, updates the generated site data, and
computes the `Since 2021` h-index and i10-index only after every deduplicated
publication has a count. If Google Scholar blocks automated requests, rerun the
same command later; already cached counts are reused.

Regenerate the BibTeX export after publication data changes:

```bash
python3 scripts/export_bibtex.py
```

OpenAlex API access can use these optional environment variables:

```bash
export OPENALEX_API_KEY=...
export OPENALEX_MAILTO=you@example.org
```

The site can be served locally with:

```bash
python3 -m http.server --directory site 8000
```

Then open `http://localhost:8000`.

## GitHub Pages Setup

For a project page under a personal account, create a repository such as
`group_google_scholar`; the site URL will normally be:

```text
https://<github-user>.github.io/group_google_scholar/
```

In the repository settings, configure Pages to deploy from GitHub Actions. The
included workflow uploads the `site/` directory as the Pages artifact.

## Adding Members

Add current members to `data/people.json`. ORCID is used as trusted evidence,
while OpenAlex author profiles provide the broad candidate list. Google Scholar
URLs are stored as outbound profile links only.

```json
{
  "id": "example-person",
  "name": "Example Person",
  "google_scholar_url": "https://scholar.google.com/citations?user=...",
  "orcid": "0000-0000-0000-0000",
  "openalex_id": ""
}
```

If an OpenAlex author profile is known to be clean, add the canonical
`openalex_id` so the site can link to it. The updater also supports
`--author-source openalex-authors`, but that is broader and can include false
positives when OpenAlex merges people with similar names.

## Curating False Positives

OpenAlex author profiles can include false positives. After each update, inspect:

```text
data/review/publication_candidates.csv
```

Rows marked `trusted_orcid` have ORCID DOI evidence. Rows marked
`candidate_openalex` came only from OpenAlex author-profile membership and should
be reviewed. To exclude a paper globally, add its DOI to `exclude_dois`. To
remove it only for one person, add it to `exclude_dois_by_person`:

```json
{
  "exclude_dois_by_person": {
    "david-pugmire": ["10.example/false-positive"]
  }
}
```

For Publish or Perish imports, false positives can be removed by Scholar result
ID or title:

```json
{
  "exclude_scholar_ids_by_person": {
    "david-pugmire": ["GS:123456789"]
  },
  "exclude_titles_by_person": {
    "david-pugmire": ["wrong publication title"]
  }
}
```
