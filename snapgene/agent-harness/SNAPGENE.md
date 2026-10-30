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
- **Bundled programs** in `Contents/Resources/Tools` run on their own: Clustal Omega
  1.2.4, MUSCLE 3.8, MAFFT (`disttbfast` only), T-Coffee, ViennaRNA 2.5 (`RNAfold`),
  parasail, samtools, CAP3. The CLI uses Clustal Omega and MUSCLE for `align`.

Result: the CLI reads SnapGene's native files directly, runs SnapGene's bundled aligners,
and uses the app only to open files for the user. It cannot render maps or run SnapGene's
own exporter, because those exist only behind the GUI.

## Data model: the `.dna` file

A list of packets: 1 byte type, 4 byte big-endian length, data.

| Type | Content |
|---|---|
| `0x09` | Cookie: `SnapGene`, sequence type (1 DNA, 2 protein, 7 RNA), export and import versions |
| `0x00` | DNA: 1 flag byte, then bases. Flags: 0x01 circular, 0x02 double-stranded, 0x04 Dam, 0x08 Dcm, 0x10 EcoKI |
| `0x20` | RNA residues (same layout as `0x00`) |
| `0x15` | Protein residues (same layout) |
| `0x0A` | Features XML. `Segment range` is 1-based inclusive. `directionality` 1 forward, 2 reverse, 3 both. `readingFrame` is signed by strand (-2 = frame 2 of a reverse feature). `<Q>/<V text|int|predef>` qualifiers; text is HTML |
| `0x05` | Primers XML. `BindingSite location` is 0-based inclusive; sites with `simplified="1"` repeat the others |
| `0x06` | Notes XML: Type, Description (HTML), Created, LastModified (`2022.9.2`), CreatedBy, Organism, References |
| `0x08` | Sticky ends and 5' modifications |
| `0x03`, `0x0D`, `0x0E`, `0x1C` | Enzyme display settings |
| `0x07`, `0x0B`, `0x1D` ... | History (compressed) |

The translation SnapGene computes for each CDS is saved in the `translation` qualifier
(segments joined with commas, `?` for an ambiguous codon). The CLI uses it as a check.

## Licence: what the Viewer locks, and what the CLI does instead

Without a licence SnapGene 8 runs as the SnapGene Viewer: it opens and draws files but
locks editing, cloning, primer design and export. The licence check is not worked around.
The locked work is done in the CLI instead, from published rules, and the result is
written as GenBank, which the Viewer opens and draws as a map.

| Locked in the Viewer | CLI | Rules it follows |
|---|---|---|
| Edit sequence, add features | `edit`, `annotate` | none needed |
| Actions > In-Fusion Cloning | `infusion` | Takara In-Fusion HD manual PT5165-1 |
| Gibson / HiFi assembly | `infusion --overlap 20..25` | same engine, longer overlap |
| Primer design | `infusion`, `pcr`, `primer-check` | Takara's windows; Primer3 for plain PCR |
| File > Export | `export` | Biopython GenBank/FASTA |
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

## Not built

Writing `.dna` files, reading GenBank or FASTA back in (input is always `.dna`), map
rendering, alignment/gel/trace input, `preview` commands, Gateway BP, MultiSite Gateway
with more than one insert, Golden Gate, plain ligation, multi-fragment assembly,
sequencing primer sets, qPCR probe design, and codon optimisation.
