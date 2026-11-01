# SnapGene: analysis for the CLI harness

Checked on this Mac on 2026-10-30 with SnapGene 8.2.3 (`/Applications/SnapGene.app`).

## Backend: what can be driven

- **No scripting.** SnapGene is a Qt app. It has no AppleScript dictionary (`.sdef`) and
  no `NSAppleScriptEnabled` key. It answers only the standard "open documents" event.
- **Server code is in the binary, but not reachable.** The binary contains the request
  handlers of SnapGene Server (`generateSVGMap`, `exportDNAFile`, `reportFeatures`,
  `reportEnzymes`, `reportORFs`, `detectFeatures` ...) and a hidden developer menu
  ("Server > Run Command..."). There is no launch flag or socket that exposes them.
  SnapGene Server is a separate licensed product.
- **Launch options** in the binary (`-open`, `-newDnaOrRnaFile`, `-saveToCollection`,
  `-restrictionEnzymes` ...) open GUI windows. They are for the GUI, not for batch work.
- **A command line exists, but it needs a licence.** `SnapGene --help` lists
  `-c/--convert <FORMAT>` (GenBank, FASTA, EMBL, DDBJ, SnapGene ...), `-I`/`-O` file
  lists for batches, `--createPreview` (a map image) and `--history` (the History view
  as an image or text). SnapGene must be closed first. Checked on 2026-11-01 with no
  licence: `--convert` and `--history` print "Please activate before using this
  command." and write nothing. `--createPreview` wrote nothing and had not finished
  after 120 s. SnapGene documents the converter in its support article "How can I
  convert file(s) into SnapGene format using the command line?".
- **Bundled programs** in `Contents/Resources/Tools` run on their own: Clustal Omega
  1.2.4, MUSCLE 3.8, MAFFT (`disttbfast` only), T-Coffee, ViennaRNA 2.5 (`RNAfold`),
  parasail, samtools, CAP3. The CLI uses Clustal Omega and MUSCLE for `align`.

Result: the CLI reads and writes SnapGene's native files directly, runs SnapGene's
bundled aligners, and uses the app only to open files for the user. It cannot render
maps or run SnapGene's own exporter: the GUI locks them, and the command-line versions
ask for a licence. With a licence, `--convert` and `--history` would let SnapGene itself
check the files this CLI writes.

## Data model: the `.dna` file

A list of packets: 1 byte type, 4 byte big-endian length, data.

| Type | Content |
|---|---|
| `0x09` | Cookie: `SnapGene`, sequence type (1 DNA, 2 protein, 7 RNA), export and import versions |
| `0x00` | DNA: 1 flag byte, then bases. Flags: 0x01 circular, 0x02 double-stranded, 0x04 Dam, 0x08 Dcm, 0x10 EcoKI |
| `0x01` | DNA packed 2 bits per base, with IUPAC and N runs (layout in sgffp's spec). None of the 203 sample files use it at the top level. The CLI does not read it |
| `0x20` | RNA residues (same layout as `0x00`) |
| `0x15` | Protein residues (same layout) |
| `0x0A` | Features XML. `Segment range` is 1-based inclusive. `directionality` 1 forward, 2 reverse, 3 both. `readingFrame` is signed by strand (-2 = frame 2 of a reverse feature). `<Q>/<V text|int|predef>` qualifiers; text is HTML |
| `0x05` | Primers XML. `BindingSite location` is 0-based inclusive; sites with `simplified="1"` repeat the others |
| `0x06` | Notes XML: Type, Description (HTML), Created, LastModified (`2022.9.2`), CreatedBy, Organism, References |
| `0x08` | `AdditionalSequenceProperties`: sticky ends and 5' modifications |
| `0x03`, `0x0D`, `0x0E`, `0x1C` | Enzyme display settings |
| `0x07` | History tree XML, xz-compressed (CRC64 check) or plain. 8 of the 12 sample files with a tree compress it. The CLI reads both and writes it compressed |
| `0x0B` | A snapshot of one parent file, so it can be reopened from the history. Per sgffp (not checked here): node ID (4 bytes), sequence type, the bases, then the parent's own packets |
| `0x1D`, `0x1E` | History modifier and history node content, xz-compressed (names from sgffp) |

The translation SnapGene computes for each CDS is saved in the `translation` qualifier
(segments joined with commas, `?` for an ambiguous codon). The CLI uses it as a check,
and leaves it out of files it writes, because SnapGene works it out from the bases and a
saved copy could be stale.

### The history tree (`0x07`)

A `HistoryTree` holding one `Node`: the file itself, with a `Node` for each file it was
made from nested under it, and so on down. A node carries `name`, `seqLen`, `circular`
and `operation` (`inFusionCloning`, `gatewayLRCloning`, `amplifyFragment`,
`insertFragment`, `insertFragments`, `goldenGateAssembly`, `primerDirectedMutagenesis`,
`flip`, `newFileFromSelection`, `changeMethylation`, `convertToSingleStranded`;
`invalid` means the file was not made in SnapGene). Under it: `Features`, `Primers`, `Oligo` elements for PCR primers,
`HistoryColors` (`ColorRange` 0-based, red for new bases, black for used ones), and one
`InputSummary` per input.

Rules found by opening written files in SnapGene 8.2.3:

- **A step needs exactly one `InputSummary` per input node.** With fewer, SnapGene quits
  as it opens the file, with no message and no crash report. `core.history.check` refuses
  such a tree before it is written. All 24 steps with inputs in the sample files have
  one `InputSummary` per input.
- `InputSummary` values given with `name1`/`name2` are positions printed next to those
  names: `Replace BamHI (444) - HindIII (474)`. Values without names are 0-based and
  printed one higher: `val1="0" val2="737"` prints `Insert 1 .. 738`.
- A node marked `resurrectable="1"` promises a snapshot packet (`0x0B`). The CLI writes
  no snapshots, so it removes the mark. SnapGene then draws the tree as usual; a parent
  simply cannot be reopened from the product.

### Other readers of the format

- **Biopython** (`Bio.SeqIO`, format `snapgene`) reads bases, topology and features.
  The tests use it as an independent check.
- **sgffp** 0.22.1 (`github.com/merv1n34k/sgffp`, MIT) reads and writes every packet
  type, snapshots and traces included. Its code and format spec were read on
  2026-11-01; it was not run. Against the 203 sample files:
  - It takes the tree of a version-15 file as always xz-compressed. Four of the twelve
    sample trees are plain, and sgffp drops them with no warning. The CLI checks the
    first byte and reads both.
  - It writes one empty `InputSummary` on a step that has none, even on a step with two
    inputs. SnapGene quit on every two-input step tried with fewer summaries than
    inputs (rule above).
  - It sorts packets by type and puts `resurrectable` and `operation` before
    `upstreamModification`. SnapGene's own files do neither. SnapGene is not known to
    care about either order.
  - It lists operations that no sample file uses (`gibsonAssembly`, `gatewayBPCloning`,
    `ligateFragments`, `digest`, `restrictionCloning`, `changeTopology` ...). These are
    names to check in SnapGene, not facts.
  - Useful later: its snapshot (`0x0B`) writer, if parents must reopen from a product.

## Licence: what the Viewer locks, and what the CLI does instead

Without a licence SnapGene 8 runs as the SnapGene Viewer: it opens and draws files but
locks editing, cloning, primer design and saving. The licence check is not worked around.
The locked work is done in the CLI instead, from published rules, and the result is
written as a SnapGene file (or GenBank), which the Viewer opens and draws as a map. The
Viewer draws the History view of a file it will not let you save.

| Locked in the Viewer | CLI | Rules it follows |
|---|---|---|
| Edit sequence, add features | `edit`, `annotate` | none needed |
| Actions > In-Fusion Cloning | `infusion` | Takara In-Fusion HD manual PT5165-1 |
| Gibson / HiFi assembly | `infusion --overlap 20..25` | same engine, longer overlap |
| Primer design | `infusion`, `pcr`, `primer-check` | Takara's windows; Primer3 for plain PCR |
| File > Export | `export` | Biopython GenBank/FASTA |
| Save as a SnapGene file | `-o name.dna` on any product | the packet layout above |
| History view of a product | written by `infusion`, `gateway` | the history rules above |
| Order a gene fragment | `gblock` | IDT gBlocks limits |
| Actions > Gateway Cloning (LR) | `gateway` | Invitrogen att site arithmetic |
| Gateway BP, Golden Gate, ligation | not built | |

### In-Fusion primer rules (PT5165-1, section IV.C and Figures 2-3)

- 15 bases at the primer's 5' end, matching the end of the opened vector. Shorter than
  12 or longer than 21 is not recommended; 20 for multi-insert work.
- The 15 bases are the bases complementary to the first 15 bases at the 5' end of one
  vector strand. So bases complementary to a 5' overhang are included, and bases of a
  3' overhang are not.
- 18-25 gene-specific bases at the 3' end, GC 40-60%, Tm 58-65 C, the two Tm values
  within 4 C, no runs of one base, and at most two G or C in the last five bases. Tm is
  measured on the gene-specific part only.
- Extra bases may sit between the two parts to rebuild a restriction site or keep the
  reading frame.

Takara's web tool (takara.teselagen.com) is a web page with no API, so the printed rules
are followed instead. The arithmetic is checked against all six primers printed in
Figure 3, which covers 5' overhangs (SalI, HindIII), blunt ends (SmaI) and 3' overhangs
(KpnI). Tm comes from Primer3 (SantaLucia 1998, 50 mM Na+, 50 nM primer), not from
SnapGene, so it can differ by about a degree. Hairpin and dimer limits (-5 and
-12 kcal/mol) are rules of thumb, not Takara numbers.

### Gateway LR rules

The LR reaction swaps the attL1-insert-attL2 part of an entry clone for the
attR1-ccdB-attR2 cassette of a destination vector: attL1 x attR1 gives attB1
and attP1, attL2 x attR2 gives attB2 and attP2.

- attL and attR share a core region, and the crossover happens inside it. The
  attB site is therefore the attR bases up to the end of the core plus the attL
  bases after it. Inside the core both sites read the same, so where exactly
  the crossover falls does not change the answer.
- The core is found by comparing the two sites, not from a fixed offset.
  SnapGene annotates attR1 and attR2 as 125 bp but attR3 and attR4 as 124 bp,
  so a fixed offset would be one base out on some vectors.
- Only sites with the same number recombine. An attL1/attL2 entry clone and an
  attR4/attR3 destination vector are refused.
- Everything between attR1 and attR2 is dropped, which is what removes ccdB
  and CmR. The other product of the reaction, the cassette between two attP
  sites, is not built.
- The product keeps the destination vector's numbering, so its map lines up
  with the empty vector.

Synthesis limits are IDT's published gBlocks limits: 125-3000 bp, GC 25-75%, no runs of
10 or more A/T or 6 or more G/C. IDT says acceptance is "complex and multifactorial", so
a clean report is not a promise.

## Mapping SnapGene actions to commands

| SnapGene GUI | CLI |
|---|---|
| Sequence window, Features tab | `features` |
| Primers tab | `primers` |
| Select range, copy bases | `seq --start --end` |
| Features > Translate | `translate --feature` |
| Show ORFs | `orfs` |
| Choose Enzymes > Unique 6+ Cutters | `enzymes` |
| Find (Cmd+F) | `find` |
| History tab | `-o name.dna`, then `open` |
| File > Export > GenBank / FASTA | `export` |
| Tools > Align Multiple Sequences | `align` |
| File > Open | `open` |
| Enzymes > digest a plasmid | `cut` |
| Primers > Design primers | `pcr` |
| Primers > check a primer | `primer-check` |
| Actions > In-Fusion Cloning | `infusion` |
| Actions > Gateway Cloning | `gateway` |
| Edit > Insert, Delete, Replace | `edit` |
| Add Feature | `annotate` |

## Verification done

- 413 `.dna` files (SnapGene samples and lab files) load; sequences equal Biopython's
  independent SnapGene reader.
- 2064 of 2064 CDS features translate to the exact protein SnapGene saved.
- pUC19: EcoRI at 396 and HindIII at 447, as on SnapGene's map.
- GenBank export of pUC19 opened in SnapGene from the CLI (`open`).
- All 413 files written back out as `.dna` and reread: same bases, same topology, same
  feature names, ranges and strands, same primers. Biopython's reader agrees on every one.

## Verification of the cloning commands

- All six In-Fusion primers of Figure 3 are reproduced base for base, for 5' overhangs,
  blunt ends and 3' overhangs.
- GFP cloned into pUC19 (EcoRI + HindIII): the backbone stays whole, the insert appears
  once, both sites are rebuilt exactly once each when read as a circle, and the AmpR and
  GFP proteins still equal the translations SnapGene saved.
- A vector opened inside one enzyme site gives a product of vector + insert + one extra
  copy of the site, with the site present twice.
- Gateway LR reproduces SnapGene's own attB1 (ACAAGTTTGTACAAAAAAGCAGGCT) and attB2
  (ACCACTTTGTACAAGAAAGCTGGGT), the sequences it labels in the plasmids it ships.
- An entry clone was run into all 77 Gateway destination vectors SnapGene ships: 74
  recombined and gave those two attB sites, and the 3 attR4/attR3 vectors were refused
  because their att numbers do not pair. In the pDEST14 product the backbone is whole,
  the ccdB and CmR cassette is gone, and the AmpR and GFP proteins are unchanged.
- Products written as `.dna` open in SnapGene 8.2.3 and draw in its History view: the
  In-Fusion product over its vector and its PCR fragment, with both primers named, and
  the Gateway product over its destination vector and entry clone, with the att sites
  named. Checked on the lab's own pENTR1A-FKBP-GFP-CterLTK and pcDNA-DEST40 files.
- Since 2026-11-01 the history tree is written xz-compressed. A Gateway product written
  that way opens in SnapGene 8.2.3 and SnapGene stays up. The History view of that
  file was not looked at by hand.

## Not built

Snapshots of parent files inside a written `.dna` (packet `0x0B`), enzyme sets and
display settings in a written `.dna`, reading GenBank or FASTA back in (input is always
`.dna`), map
rendering, alignment/gel/trace input, `preview` commands, Gateway BP, MultiSite Gateway
with more than one insert, Golden Gate, plain ligation, multi-fragment assembly,
sequencing primer sets, qPCR probe design, and codon optimisation.
