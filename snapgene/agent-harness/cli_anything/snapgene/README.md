# cli-anything-snapgene

Command-line tool for SnapGene files on macOS. It reads `.dna`, `.rna` and `.prot`
files, lists features and primers, finds restriction sites, motifs and ORFs, translates
CDS features, exports GenBank or FASTA, aligns sequences with SnapGene's own aligners,
searches folders of plasmids, and opens files in the SnapGene app.

It also does the work SnapGene keeps behind a paid licence: cut a plasmid, design
Takara In-Fusion primers, build the product plasmid, check a fragment before you order
it as synthetic DNA, edit a sequence, and add features. Those commands write GenBank,
which the free SnapGene Viewer opens and draws as a map.

## What it uses from SnapGene

SnapGene for macOS has no scripting interface and no headless mode. This tool uses:

- **SnapGene's file format**, read directly (packets in the `.dna` file).
- **SnapGene's bundled aligners**: Clustal Omega and MUSCLE from
  `SnapGene.app/Contents/Resources/Tools`.
- **The SnapGene app**, to open files for you to look at (`open` command).

It does **not** edit `.dna` files. It never writes to an input file. Products and edits
come out as GenBank.

Restriction sites come from REBASE (through Biopython), the same source SnapGene uses.
Dam/Dcm methylation blocking is not applied.

In-Fusion primer rules come from Takara's In-Fusion HD manual (PT5165-1, section IV.C
and Figures 2 and 3): 15 bases of vector homology at the 5' end, 18-25 gene-specific
bases at the 3' end, Tm 58-65 C measured on the gene-specific part only, GC 40-60%, the
two Tm values within 4 C, and no more than two G or C in the last five bases. Bases of a
5' overhang count inside the 15; bases of a 3' overhang do not. Takara's own web tool is
a web page and cannot be called from a terminal, so this tool follows the printed rules;
its arithmetic is checked against all six primers printed in Figure 3. Tm comes from
Primer3 (SantaLucia 1998, 50 mM Na+, 50 nM primer), so it can differ from SnapGene's
number by about a degree. Synthesis limits are IDT's published gBlocks limits.

## Install

1. Install SnapGene in `/Applications` (or set `SNAPGENE_APP` to the app path).
2. Install the CLI (from the repo root):

```bash
python3 -m venv ~/.venvs/cli-anything-snapgene
~/.venvs/cli-anything-snapgene/bin/pip install -e "snapgene/agent-harness[dev]"
```

The command is `~/.venvs/cli-anything-snapgene/bin/cli-anything-snapgene`.

## Usage

Positions are 1-based and inclusive, as in the SnapGene window. On a circular file a range
with start after end crosses the origin. Every command takes `--json`.

```bash
cli-anything-snapgene status                          # SnapGene version, aligners found
cli-anything-snapgene info pUC19.dna                  # length, topology, counts, notes
cli-anything-snapgene features pUC19.dna --type CDS   # features with ranges and strands
cli-anything-snapgene primers pGFP.dna                # primers and binding sites
cli-anything-snapgene seq pUC19.dna --start 396 --end 452
cli-anything-snapgene seq pUC19.dna --feature AmpR    # in the feature's direction
cli-anything-snapgene translate pUC19.dna --feature AmpR
cli-anything-snapgene orfs pUC19.dna --min-aa 100
cli-anything-snapgene enzymes pUC19.dna               # unique cutters, sites of 6+ bases
cli-anything-snapgene enzymes pUC19.dna --enzyme EcoRI,BamHI,HindIII
cli-anything-snapgene find pUC19.dna GGATCC           # IUPAC codes allowed, both strands
cli-anything-snapgene export pUC19.dna                # writes pUC19.gb next to the input
cli-anything-snapgene export pUC19.dna --format fasta -o /tmp/pUC19.fasta
cli-anything-snapgene align a.dna b.dna --tool muscle -o ab.fasta
cli-anything-snapgene search ~/plasmids --feature mCherry
cli-anything-snapgene search ~/plasmids --seq GAATTC --name pENTR
cli-anything-snapgene open pUC19.gb                   # opens it in the SnapGene window
cli-anything-snapgene                                 # interactive shell
```

`translate --feature` also says whether the protein matches the translation SnapGene
saved in the file.

## Cloning, ordering and editing

```bash
# What do these enzymes do to the plasmid?
cli-anything-snapgene cut pUC19.dna --enzyme EcoRI,HindIII

# In-Fusion a gene into a cut vector: two primers, the fragment, the product
cli-anything-snapgene infusion pUC19.dna --enzyme EcoRI --enzyme HindIII \
    --insert pCMV-mCherry.dna --insert-feature mCherry -o pUC19-mCherry.gb

# A fusion protein: leave the insert's stop codon out, and name the insert
cli-anything-snapgene infusion pENTR1A-FKBP-GFP-CterLTK.dna --remove 834-1550 \
    --insert pCMV-mCherry.dna --insert-range 2127-2834 --insert-name mCherry

# No enzyme: open the plasmid at one base (inverse PCR), or swap a part out
cli-anything-snapgene infusion pENTR1A.dna --at 1250 --insert insert.dna
cli-anything-snapgene infusion pENTR1A.dna --remove 500-1200 --insert insert.dna

# Gibson or HiFi assembly: the same command with a longer overlap
cli-anything-snapgene infusion vector.dna --at 100 --insert gene.dna --overlap 25

# Check a fragment against IDT's gBlocks limits, and write the FASTA to order
cli-anything-snapgene gblock plasmid.dna --feature mCherry -o order.fasta

# Edit: put bases in, take bases out, swap a part
cli-anything-snapgene edit plasmid.dna --at 1250 --sequence GGCGGCGGC --label "GS linker"
cli-anything-snapgene edit plasmid.dna --range 500-700
cli-anything-snapgene edit plasmid.dna --feature mCherry --sequence ATG...

# Add a feature
cli-anything-snapgene annotate plasmid.dna --name "my insert" --range 500-1200 --type CDS

# Plain PCR primers for a feature or a range (Primer3)
cli-anything-snapgene pcr plasmid.dna --feature mCherry
cli-anything-snapgene pcr plasmid.dna --range 500-1200 --max-product 900 -o with-primers.gb

# Check a primer you already have against a file
cli-anything-snapgene primer-check plasmid.dna GTAAAACGACGGCCAGT
```

`pcr` keeps the whole target inside the product and puts the primers in the flanks. It
reports the pairs Primer3 ranks best, with Tm, GC, where each primer binds and the product
size. `-o` writes a GenBank copy of the file with the pairs on it as primers.

`primer-check` says where a primer matches exactly, on either strand, counting the origin,
and gives its Tm, GC, hairpin and self dimer.

`infusion` writes the product as GenBank next to the vector unless you give `-o`, and
`--fragment-out` writes the fragment to order as FASTA. The fragment is the PCR product:
forward primer, insert, reverse primer. Order it as a gBlock, or amplify it.

Every join rebuilds the restriction site unless you pass `--no-restore-sites`, the same
extra bases Takara's Figure 3 adds. Every check that fails is printed, and `--json`
reports it under `warnings` and `fragment.synthesis_warnings`. A feature that the change
cuts in half is left out of the product and named.

The product keeps the vector's own numbering: base 1 of the vector stays base 1 of the
product, so the map is not renumbered.

`edit` and `infusion` never touch the input file.

### Errors

Errors exit with code 1. With `--json`: `{"ok": false, "error": "...", "code": "..."}`.
Codes: `no_file`, `bad_file`, `unsupported_file`, `not_dna`, `not_circular`,
`feature_not_found`, `ambiguous_feature`, `bad_range`, `bad_motif`, `bad_sequence`,
`bad_request`, `unknown_enzyme`, `no_cut`, `too_many_cuts`, `bad_insert`, `bad_overlap`,
`output_exists`, `output_is_input`, `bad_output`, `too_few_sequences`, `mixed_types`,
`tool_error`, `tool_timeout`, `snapgene_missing`, `no_folder`, `no_criteria`,
`open_failed`.

## Tests

```bash
~/.venvs/cli-anything-snapgene/bin/pytest snapgene/agent-harness/cli_anything/snapgene/tests -v
```

End-to-end tests read the sample files inside the SnapGene app and run its aligners.
They fail if SnapGene is not installed. To also test `open` (it opens a SnapGene window):

```bash
SNAPGENE_GUI=1 ~/.venvs/cli-anything-snapgene/bin/pytest snapgene/agent-harness/cli_anything/snapgene/tests -v
```

## Not supported

- Writing `.dna` files. Edits and products come out as GenBank, which SnapGene and the
  free SnapGene Viewer open.
- Alignment (`.dnaln`), gel, trace and collection files as input.
- Rendering maps (SnapGene draws maps only in its window). No `preview` command.
- Gateway BP and LR, Golden Gate, and ligating a cut insert into a cut vector.
- Primers for sequencing a long region (one `pcr` call designs one pair) and qPCR
  specific checks such as exon junction or probe design.
- Multi-fragment assembly (one insert at a time).
- Codon optimisation, and splitting a long fragment into several gBlocks.
