---
name: "cli-anything-endnote"
description: >-
  CLI for EndNote 2025 on macOS. Adds PubMed papers to the open EndNote library by PMID,
  and turns [PMID ...] markers in a Word .docx into EndNote temporary citations.
---

# cli-anything-endnote

Use this tool when a Word document has citation markers like `[PMID 17625570; 41528114]`
and the user wants them as real EndNote citations, or when the user wants papers added to
EndNote by PMID.

## Before you run it

- EndNote 2025 must be open with exactly one library: the library for this project.
- The first run makes macOS ask for permission to control EndNote. The user must click OK.

## Commands

```bash
cli-anything-endnote status                     # is EndNote open, which library, how many references
cli-anything-endnote add 17625570 41528114      # add papers by PMID; skips papers already there
cli-anything-endnote add --file PMIDS.txt       # one or more PMIDs per line; lines starting with # are ignored
cli-anything-endnote find 17625570              # record number, citation, title, or "not found"
cli-anything-endnote link Narrative.docx        # writes Narrative_LINKED.docx; never changes the input
cli-anything-endnote link Narrative.docx --output Out.docx --overwrite
```

Add `--json` before or after the command name for machine-readable output.
With no command, the tool starts an interactive shell.

## Marker format for Word files

Write markers as `[PMID 12345678]` or `[PMID 12345678; 23456789]`. Also accepted:
`[PMID: 1]`, `[PMIDs 1; 2]`, `[pmid 1, 2]`. Highlight is optional.

`link` replaces each marker with EndNote's own temporary citation, for example
`{Soda, 2007 #18; Siegel, 2026 #2}`, adds missing papers to the library first, and leaves
a marker unchanged (still highlighted) if one of its PMIDs could not be added.

## After `link`

Tell the user: open the `_LINKED.docx` file in Word, keep only the same library open in
EndNote, and click **EndNote > Update Citations and Bibliography**. If the file has tracked
changes, accept them first.

## JSON output

- `status`: `{"ok", "running", "libraries", "record_count"}`
- `add`: `{"ok", "library", "added", "skipped", "failed", "warnings"}`; each record is
  `{"pmid", "rec_number", "citation", "title"}`; each failure is `{"pmid", "reason"}`
- `find`: `{"ok", "records": {pmid: record or null}, "warnings"}`
- `link`: `{"ok", "library", "input", "output", "markers_found", "markers_linked",
  "markers_left", "pmids_added", "pmids_already_in_library", "failed", "warnings"}`
- `link` with no markers in the file: only `{"ok", "input", "output": null, "markers_found": 0}`, and no file is written.
- Errors: `{"ok": false, "error", "code"}` with exit code 1.
- Exit code 1 can also come with `"ok": true`: `add` when every PMID failed, and `link` when no PMID could be added (then `"output"` is null and no file is written). Always check `failed` and `output`, not only `ok`.

## Error codes

| Code | What to do |
|---|---|
| `endnote_missing` | EndNote 2025 is not in /Applications. |
| `no_library` | Ask the user to open EndNote and the project library. |
| `many_libraries` | Ask the user to close every library except the project library. |
| `automation_denied` | Ask the user to allow the terminal in System Settings > Privacy & Security > Automation. |
| `endnote_timeout` | EndNote is blocked, often by a dialog. Ask the user to close it. |
| `applescript_error` | Report the message to the user. |
| `pubmed_error` | Network problem. Try again later. |
| `output_exists` | Use `--overwrite` or `--output`. |
| `output_is_input` | Choose a different `--output`. |
| `bad_docx` | The file is not a .docx. |
| `no_pmids` | Give PMIDs or `--file`. |
