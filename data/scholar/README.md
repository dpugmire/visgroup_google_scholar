# Scholar Data

This folder stores Google Scholar data imported from Publish or Perish.

- `raw/`: copied Publish or Perish JSON snapshots, one file per person.
- Generated site data is written to `site/data/`.

The importer expects Google Scholar Profile queries in Publish or Perish, not
broad author-name searches. Use the Scholar profile IDs from `data/people.json`.
