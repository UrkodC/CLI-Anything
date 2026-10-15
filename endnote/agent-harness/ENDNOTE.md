# EndNote 2025: analysis and SOP for the agent harness

## Surfaces used

- **AppleScript dictionary** (`EndNote 2025.app/Contents/Resources/EndNote.sdef`). The CLI uses:
  `find ... in field "Accession Number"`, `field ... of record`, `unformatted record`,
  `import ... into document 1`, `retrieve "all" records in document 1`, `name of every document`,
  and (tests only) `delete record`.
- **NCBI E-utilities** `efetch` for PubMed records.
- **The .docx file** (a ZIP of XML parts). Only `word/document.xml` is changed, in a copy.

## Surfaces not used

- The `.enl` file is a SQLite database. EndNote locks it while the library is open. The CLI
  never reads or writes it directly, so it cannot damage a library.
- Cite While You Write in Word is not driven by the CLI. The user clicks
  Update Citations and Bibliography once.

## Facts confirmed on 2026-10-15 (EndNote 22.3.0.22265)

- `find` returns IDs like `<RecordID database="1" recNum="2" />`. The `recNum` is the number
  that appears after `#` in a temporary citation.
- `unformatted record` returns the temporary citation, for example `{Siegel, 2026 #2}`.
- `find` matches substrings: `4152811` finds the record whose PMID is `41528114`.
  The CLI keeps a hit only when the Accession Number equals the PMID.
- `find` does not return records that are in EndNote's Trash.
- PubMed imports store the PMID in Accession Number, the DOI in Electronic Resource Number,
  and the PMCID in Custom 2.
- `export ... as "RSXML"` gives the layout that `enxml.py` copies.

## Known limits

- macOS and EndNote 2025 only.
- Markers in footnotes, endnotes, headers and comments are not processed.
- PDFs are not attached.
- An open dialog in EndNote blocks AppleScript. The CLI stops with `endnote_timeout`.
