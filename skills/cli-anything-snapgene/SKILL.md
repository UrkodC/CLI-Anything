---
name: "cli-anything-snapgene"
description: >-
  CLI for SnapGene files on macOS. Reads .dna/.rna/.prot plasmid and sequence files:
  features, primers, sequence ranges, translation, ORFs, restriction sites, motif search.
  Exports GenBank or FASTA, aligns sequences with SnapGene's bundled Clustal Omega or
  MUSCLE, searches folders of plasmids, and opens files in the SnapGene app. Also clones:
  cuts a plasmid with enzymes, designs Takara In-Fusion (or Gibson/HiFi) primers, builds
  the product plasmid, checks a fragment against IDT gBlocks limits, edits a sequence and
  adds features.
---

# cli-anything-snapgene

Use this tool when the user asks about a SnapGene file (`.dna`, `.rna`, `.prot`): what is
in a plasmid, where a feature or site is, the protein a CDS makes, which plasmid in a folder
has a given insert, or when a GenBank/FASTA copy is needed for another tool.

Use it as well when the user wants to clone something (In-Fusion, Gibson or HiFi), design
the primers for it, order a gene fragment, or change a sequence. The paid SnapGene cloning
tools are not needed: these commands write GenBank, which the free SnapGene Viewer draws.

## Before you run it

- Command: `~/.venvs/cli-anything-snapgene/bin/cli-anything-snapgene` (or
  `cli-anything-snapgene` if it is on PATH).
- `status` confirms SnapGene is installed. SnapGene does not need to be running.
- Add `--json` to every command when you will parse the output.

## Commands

```bash
cli-anything-snapgene status
cli-anything-snapgene info FILE.dna
cli-anything-snapgene features FILE.dna [--type CDS]
cli-anything-snapgene primers FILE.dna
cli-anything-snapgene seq FILE.dna [--start N --end M | --feature NAME | --feature-index I] [--revcomp]
cli-anything-snapgene translate FILE.dna --feature NAME            # or --start/--end/--strand
cli-anything-snapgene orfs FILE.dna [--min-aa 75]
cli-anything-snapgene enzymes FILE.dna [--enzyme EcoRI,BamHI] [--max-cuts 1] [--min-site 6]
cli-anything-snapgene find FILE.dna MOTIF                          # IUPAC, both strands
cli-anything-snapgene export FILE.dna [--format genbank|fasta] [-o OUT] [--overwrite]
cli-anything-snapgene align A.dna B.dna [C.fasta ...] [--tool clustalo|muscle] [-o OUT.fasta]
cli-anything-snapgene search FOLDER [--name TEXT] [--feature TEXT] [--seq MOTIF]
cli-anything-snapgene open FILE                                    # shows it in the SnapGene app
```

### Cloning and editing

```bash
cli-anything-snapgene cut FILE.dna --enzyme EcoRI,HindIII
cli-anything-snapgene infusion VECTOR.dna (--enzyme E [--enzyme E2] | --at N | --remove A-B) \
    (--insert FILE.dna [--insert-feature NAME | --insert-range A-B] | --insert-seq BASES) \
    [--insert-name NAME] [--insert-revcomp] [--overlap 15] [--no-restore-sites] \
    [--keep larger|smaller] \
    [-o PRODUCT.gb] [--fragment-out ORDER.fasta] [--no-product] [--overwrite]
cli-anything-snapgene gblock FILE.dna [--feature NAME | --range A-B] [--flank5 S] [--flank3 S] [-o F.fasta]
cli-anything-snapgene edit FILE.dna (--at N --sequence BASES | --range A-B [--sequence BASES] \
    | --feature NAME [--sequence BASES]) [--label NAME] [-o OUT.gb]
cli-anything-snapgene annotate FILE.dna --name NAME --range A-B [--type CDS] [--strand +|-] [-o OUT.gb]
cli-anything-snapgene pcr FILE.dna (--feature NAME | --range A-B) [--context 600] [--pairs 3] \
    [--opt-tm 60] [--min-tm 57] [--max-tm 63] [--min-product N] [--max-product N] [-o OUT.gb]
cli-anything-snapgene primer-check FILE.dna PRIMER_BASES
```

## Rules for agents

- Positions are 1-based and inclusive, as in SnapGene. On circular files `start > end`
  means the range crosses the origin. A `-` strand feature or ORF still has `start < end`
  (a top-strand range); `seq --feature` returns its bases in reading direction.
- Feature names can repeat. If you get `ambiguous_feature`, use `--feature-index` with the
  `index` from `features --json`.
- `translate --feature` returns `matches_snapgene`: `true` means the protein equals the
  translation SnapGene saved; `false` is worth reporting to the user.
- `enzymes` without `--enzyme` lists unique cutters with sites of 6+ bases from commercial
  REBASE enzymes (isoschizomers included). Positions are the last base before the top-strand
  cut, the number SnapGene shows on the map. Methylation blocking is not checked.
- `export` and `align -o` refuse to overwrite unless `--overwrite` is given. The tool never
  writes to `.dna` files.
- For clone verification against a sequencing read, export the reference with
  `export --format genbank` and hand it to the `plasmid-verify` skill.
- `open` puts a window on the user's screen. Use it only when the user wants to look.
- Errors: exit code 1, JSON `{"ok": false, "error": "...", "code": "..."}`.

## Rules for cloning

- `infusion` follows Takara's printed In-Fusion rules (manual PT5165-1). Report every
  line of `warnings` and `fragment.synthesis_warnings` to the user; they are the rules the
  design could not satisfy, and the user decides. Never present a design as clean when
  that list is not empty.
- The 15 bases of homology are not a guess: they are the vector's own end bases. A 5'
  overhang is inside them, a 3' overhang is not. Do not hand-edit the primers.
- Tm is Primer3's, measured on the gene-specific part only, and can differ from
  SnapGene's by about a degree. Say so if the user compares numbers.
- `fragment_sequence` is the PCR product, which is also the gene fragment to order.
  `--fragment-out` writes it as FASTA.
- Raise `--overlap` to 20-25 for Gibson or HiFi assembly. Takara's range is 12-21.
- The product is GenBank. Tell the user to open it with `open PRODUCT.gb` to see the map.
  Nothing is written over without `--overwrite`, and input files are never changed.
- `dropped_features` names features the change cut in half. Pass that on: a lost feature
  usually means the user cut inside something they cared about.
- `edit` reports `protein_changes` when a CDS protein came out different. That is how a
  frame shift shows up.
- For a fusion protein, leave the insert's stop codon out (`--insert-range` three bases
  short) or nothing after it is translated. `infusion` warns when the insert ends with a
  stop codon, and when its length is not a multiple of 3.
- `--insert-name` names the insert feature and both primers. Use it when the insert comes
  from a range, so the product does not carry a name like "plasmid 2127-2834".
- `pcr` designs plain PCR primers with Primer3 and keeps the target whole; the primers sit
  in the flanks, so the file needs sequence on both sides of the target. `pairs` comes back
  best first. `primer-check` tests a primer the user already has.
- Not supported: Gateway BP/LR, Golden Gate, plain ligation, more than one insert at a
  time, and qPCR or sequencing primer sets. Say so instead of improvising.

## Examples

```bash
# What is in this plasmid?
cli-anything-snapgene info pCMV-mCherry.dna --json
cli-anything-snapgene features pCMV-mCherry.dna --json

# Which lab plasmids carry FKBP, and is EcoRI unique in them?
cli-anything-snapgene search "CLIP170-LTK project" --feature FKBP --json
cli-anything-snapgene enzymes pENTR1A-FKBP-GFP-CterLTK.dna --enzyme EcoRI --json

# Protein of a CDS
cli-anything-snapgene translate pENTR1A-GFP-CLIP170LTK.dna --feature "LTK C'ter" --json

# In-Fusion mCherry into pUC19 cut with EcoRI and HindIII
cli-anything-snapgene infusion pUC19.dna --enzyme EcoRI --enzyme HindIII \
    --insert pCMV-mCherry.dna --insert-feature mCherry \
    -o pUC19-mCherry.gb --fragment-out mCherry-order.fasta --json

# Swap a tag out of a plasmid, no enzyme needed
cli-anything-snapgene infusion pENTR1A-GFP-CLIP170LTK.dna --remove 834-1550 \
    --insert mCherry.dna --json
```
