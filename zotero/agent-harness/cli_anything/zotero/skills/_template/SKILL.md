# _template — placeholder Zotero skill

Rename this folder to your skill slug (e.g. `lit_triage`, `alk_corpus`,
`bibliography_audit`) and fill in the stages below. Mirrors the layout of
fiji's `mt_clip170` reference skill.

A Zotero skill is the right place when you want to:

- pull a slice of your library on a recurring basis (a collection, a saved search, a tag),
- enrich it (cross-reference PMIDs, dedupe, annotate),
- and emit a structured artifact (CSV/JSON/BibTeX/Markdown) you can hand to another tool.

## Stages

| # | Name | Purpose | Output |
|---|------|---------|--------|
| 0 | fetch   | Query Zotero (collection / search / tag) and dump raw items | `00_raw_items.json` |
| 1 | filter  | Apply filters (year range, item type, tag whitelist) + dedupe | `01_filtered.json` |
| 2 | extract | Pull metadata, abstract, notes, tags, attached PDF paths     | `02_enriched.json` |
| 3 | export  | Render final artifact (CSV/BibTeX/Markdown bibliography)     | `corpus.csv`, `corpus.bib`, `summary.md` |

## Commands

```
# Full pipeline against a Zotero collection
python3 -m cli_anything.zotero _template run --collection "ALK fusions"

# Same, but against a saved search
python3 -m cli_anything.zotero _template run --search "saved-search-key"

# Iterate on a subset of stages with logs
python3 -m cli_anything.zotero _template tune --collection "ALK fusions" --stages 0-1

# Override a parameter from the CLI
python3 -m cli_anything.zotero _template run --collection "ALK fusions" --set filter.year_min=2018

# Print current parameters
python3 -m cli_anything.zotero _template params
```

Output is written to `./_template_out/<collection_or_query>/<timestamp>/`.

## Dependencies

- Zotero desktop running locally with the Local API enabled
  (`cli-anything-zotero app enable-local-api --launch`).
- Python: `click`, `pyyaml`, `requests` (or whatever the parent zotero CLI already uses).

## Tuning workflow

1. `tune --stages 0` — confirm the right items came back from Zotero.
2. `tune --stages 1` — adjust `filter.*` until the corpus matches what you want.
3. `tune --stages 2` — sanity-check enrichment (no empty abstracts where there shouldn't be).
4. `run` end-to-end and inspect `summary.md`.
