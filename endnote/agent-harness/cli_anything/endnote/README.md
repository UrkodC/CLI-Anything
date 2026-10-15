# cli-anything-endnote

Add PubMed papers to EndNote 2025 and turn `[PMID ...]` markers in Word files into
EndNote citations. macOS only.

## What you need

- macOS with EndNote 2025 in `/Applications`.
- Microsoft Word with the EndNote tab (Cite While You Write).
- Python 3.10 or newer.
- Internet access (PubMed).

## Install

```bash
python3 -m venv ~/.venvs/cli-anything-endnote
~/.venvs/cli-anything-endnote/bin/pip install -e "endnote/agent-harness[dev]"
```

The first command that talks to EndNote makes macOS ask if your terminal may control
EndNote 2025. Click OK. To change it later: System Settings > Privacy & Security > Automation.

## Use

1. Open EndNote. Open only the library for this project.
2. Check: `cli-anything-endnote status`
3. Add papers: `cli-anything-endnote add 17625570 41528114` or `cli-anything-endnote add --file PMIDS.txt`
4. Link a Word file: `cli-anything-endnote link "Narrative.docx"`. This writes `Narrative_LINKED.docx`.
   Your original file is not changed.
5. Open `Narrative_LINKED.docx` in Word. Click EndNote > Update Citations and Bibliography.

Add `--json` to any command for machine-readable output. Run `cli-anything-endnote` with
no command for the interactive shell.

Markers look like `[PMID 17625570]` or `[PMID 17625570; 41528114]`. A marker with a PMID
that could not be added stays as it is, so you can find it.

## Tests

```bash
# unit tests (no EndNote, no network)
~/.venvs/cli-anything-endnote/bin/pytest endnote/agent-harness/cli_anything/endnote/tests -v

# real PubMed
PUBMED_NETWORK=1 ~/.venvs/cli-anything-endnote/bin/pytest endnote/agent-harness/cli_anything/endnote/tests -v

# real EndNote: create ~/EndNoteCLI_Test.enl once (File > New), open only that library
ENDNOTE_E2E=1 PUBMED_NETWORK=1 ~/.venvs/cli-anything-endnote/bin/pytest endnote/agent-harness/cli_anything/endnote/tests -v

# a real Word file with markers (read only; the test works on a copy)
ENDNOTE_REAL_DOCX="/path/to/file.docx" ~/.venvs/cli-anything-endnote/bin/pytest endnote/agent-harness/cli_anything/endnote/tests/test_word_link.py -v
```

Set `NCBI_API_KEY` to raise the PubMed rate limit from 3 to 10 requests per second.
