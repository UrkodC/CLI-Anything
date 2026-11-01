# cli-anything-snapgene: test plan and results

## Part 1: plan

### Test inventory

| File | Kind | Needs SnapGene | Planned tests |
|---|---|---|---|
| `test_dnafile.py` | unit | no | 12 |
| `test_seqtools.py` | unit | no | 16 |
| `test_enzymes.py` | unit | no | 6 |
| `test_convert.py` | unit | no | 9 |
| `test_align.py` | unit | no | 5 |
| `test_cli.py` | unit (Click runner) | no | 10 |
| `test_full_e2e.py` | end to end | yes | 12 |

Unit tests build small `.dna` and `.prot` files in a temp folder with `_helpers.py`
(the same packet layout SnapGene writes). They never read SnapGene's own files, so they
run on any machine.

### Unit test plan

- `dnafile.py`: sequence, topology and methylation flags; features with several segments,
  HTML in qualifier text, reading frame 2; primers (0-based binding sites become 1-based,
  "simplified" duplicate sites dropped); notes; errors for a non-SnapGene file, a cut-short
  file, an unsupported SnapGene type, a file with no sequence; `find_feature` by name
  (case-insensitive), by index, ambiguous name, missing name.
- `seqtools.py`: ranges on linear and circular files (crossing the origin, out of range,
  start after end on linear); feature sequence on the minus strand and across the origin;
  coding sequence in frame 2; motif search on both strands, IUPAC codes, a palindrome
  reported once, a match across the origin (circular only), protein motifs, bad motifs;
  ORFs on both strands with correct top-strand ranges, across the origin, minimum length,
  protein file error.
- `enzymes.py`: cut position convention (last base before the cut), a site across the
  origin, unknown enzyme name, default "unique 6+" filter, protein error.
- `convert.py`: GenBank written and read back with Biopython (sequence, topology, features,
  primers as `primer_bind`); minus-strand join extracts the same bases as
  `feature_sequence`; feature across the origin; `codon_start` for frame 2; date format;
  FASTA header; `output_exists`, `output_is_input`, `bad_output`.
- `align.py`: pairwise identity math; duplicate names get a suffix; mixed DNA and protein,
  fewer than two sequences, unsupported file type.
- `snapgene_cli.py`: `--json` shape (`ok`, error `code`, exit 1) for each command group,
  group-level `--json`, human output for `features` and `seq`.

### End-to-end plan (`test_full_e2e.py`)

Uses the real SnapGene 8 install and its sample files in
`SnapGene.app/Contents/Resources` (read-only, never copied into the repo).

1. Every `.dna` file shipped with SnapGene loads, and its sequence equals what Biopython's
   independent SnapGene reader returns.
2. Every CDS in those files translates to the exact protein SnapGene saved in the file.
3. pUC19 (sample project): EcoRI cuts after 396, HindIII after 447, the numbers on
   SnapGene's map.
4. GenBank export of pUC19: Biopython reads it back; AmpR (two segments, minus strand)
   extracts to the protein SnapGene saved.
5. Alignment through SnapGene's bundled Clustal Omega and MUSCLE: output is valid FASTA,
   same length rows, identity of GFP vs GFP-with-primer-tails is above 90%.
6. Subprocess tests through the installed `cli-anything-snapgene` command: `--help`,
   `status --json`, `info --json`, `export`, `align -o`, `search`, error JSON.
7. Opt-in (`SNAPGENE_GUI=1`): `open` an exported GenBank file in SnapGene and confirm the
   app is running afterwards. Off by default because it opens a window.

### Realistic workflows

- **Clone check prep**: `info` then `features` then `export --format genbank` of a plasmid,
  so the GenBank goes to the `plasmid-verify` skill. Verified: GenBank parses, features keep
  their ranges.
- **Find the right plasmid**: `search FOLDER --feature mCherry`, then `enzymes` on the hit
  for a unique cutter. Verified: hits list the feature with its range.
- **Compare two constructs**: `align a.dna b.dna --tool muscle -o out.fasta`. Verified:
  aligned FASTA rows have equal length; identity is reported for the pair.

### Manual check

Open one exported GenBank file in SnapGene (`cli-anything-snapgene open FILE.gb`) and
confirm the features show on the map with the same names and ranges.

## Part 1b: plan for cloning, editing and primer design

Added after the read-only commands were done, for `cut`, `infusion`, `gblock`,
`edit` and `annotate`.

### Ground truth

Takara's In-Fusion HD manual (PT5165-1) prints six finished primers in Figure 3,
for a vector opened three ways: 5' overhangs (SalI and HindIII), blunt ends
(SmaI) and 3' overhangs (KpnI). The figure also prints the vector sequence
around each site. A test plasmid is built that holds those exact bases, so the
tool's primers can be compared with Takara's own, base for base. This is the
only outside ground truth for In-Fusion primer design that does not need a
licence, and it covers every end type.

### Test inventory

| File | Kind | Needs SnapGene | Planned tests |
|---|---|---|---|
| `test_cutting.py` | unit | no | 8 |
| `test_primers.py` | unit | no | 8 |
| `test_assemble.py` | unit | no | 8 |
| `test_infusion.py` | unit | no | 16 |
| `test_gblock.py` | unit | no | 7 |
| `test_edit.py` | unit | no | 11 |
| `test_cli.py` (added to) | unit | no | 7 |
| `test_full_e2e.py` (added to) | end to end | yes | 3 |

### Unit test plan

- `cutting.py`: cut positions and end type for a 5' overhang (EcoRI), a 3'
  overhang (KpnI) and a blunt cut (SmaI); the `restore_before` and
  `restore_after` bases of each; an enzyme that cuts outside its site (BsaI)
  restores nothing; a site across the origin; the pieces a double digest makes;
  a linear file keeps its own ends.
- `primers.py`: GC, Tm, longest run, 3'-end G/C count; `choose_anchors` keeps
  both Tm values in Takara's window and close together; each rule in
  `warnings_for` fires on a primer that breaks it and stays quiet on one that
  does not.
- `assemble.py`: a feature inside a piece moves and keeps its qualifiers; a
  feature cut in half is dropped and reported; a feature outside the piece is
  not reported; a flipped piece turns a `+` feature into `-` and keeps its
  protein; a feature across the origin; a piece longer than the file (a vector
  opened inside one site) maps features to the first copy; `relocate_primers`
  keeps a primer that still binds and drops one that does not.
- `infusion.py`: **the three Figure 3 cases, primer for primer**; the 15 bases
  come from the right strand end (5' overhang bases in, 3' overhang bases out);
  the product keeps the backbone whole, holds the insert once, and rebuilds the
  enzyme site at both joins; one enzyme that cuts once gives a product that is
  vector + insert + one site; `--no-restore-sites` leaves the site out and warns;
  `--at` and `--remove` open the vector without enzymes; two cuts pick the larger
  piece unless told otherwise; a reversed insert flips its features; errors for
  no cut, too many cuts, a linear vector, a too-short insert and a silly overlap;
  an overlap outside 12-21 warns but still works.
- `gblock.py`: too short, too long, GC too low and too high, a run of 6 G, a run
  of 10 A, a repeated 20-base stretch, and a clean fragment with no warnings.
- `edit.py`: bases put in at a point, at base 0 and at the last base; a part
  deleted; a part swapped for new bases; a part across the origin; features after
  the change move by the right amount; a feature cut in half is dropped; the CDS
  protein check reports a frame shift; `annotate` adds a feature in the right
  place; bad bases and bad ranges are refused.
- `snapgene_cli.py`: `--json` shape for `cut`, `infusion`, `gblock`, `edit` and
  `annotate`; `infusion` refuses to overwrite; `edit` writes GenBank that
  Biopython reads back; error JSON for a bad range.

### End-to-end plan (added to `test_full_e2e.py`)

Uses only files SnapGene ships, so the tests run on any machine with SnapGene.

1. pUC19 cut with EcoRI and HindIII gives the two pieces SnapGene's map shows
   (51 bp and 2635 bp), with 5' overhangs.
2. GFP.dna cloned into pUC19 (EcoRI + HindIII) through `infusion`: the product
   keeps the whole backbone, holds GFP once, has exactly one EcoRI and one
   HindIII site when read as a circle, and the AmpR and GFP proteins still match
   the translations SnapGene saved.
3. The same through the installed command, with `--json`, and the product opens
   as GenBank in Biopython.

### Manual check

Open a product in SnapGene (`cli-anything-snapgene open PRODUCT.gb`) and confirm
the map shows the insert, the rebuilt sites and the two In-Fusion primers.

## Part 2: results

Run on 2026-10-30, macOS, SnapGene 8.2.3, Python 3.14, Biopython 1.88, installed command
(`CLI_ANYTHING_FORCE_INSTALLED=1`).

```
test_align.py::test_pairwise_identity_skips_double_gaps PASSED [  1%]
test_align.py::test_duplicate_names_get_suffix PASSED [  2%]
test_align.py::test_mixed_types PASSED [  3%]
test_align.py::test_too_few_sequences PASSED [  4%]
test_align.py::test_unsupported_file PASSED [  6%]
test_cli.py::test_info_json PASSED [  7%]
test_cli.py::test_group_level_json PASSED [  8%]
test_cli.py::test_features_human PASSED [  9%]
test_cli.py::test_seq_feature_minus PASSED [ 11%]
test_cli.py::test_seq_needs_both_ends PASSED [ 12%]
test_cli.py::test_translate_checks_snapgene_translation PASSED [ 13%]
test_cli.py::test_translate_protein_file_fails PASSED [ 14%]
test_cli.py::test_find_orfs_enzymes PASSED [ 16%]
test_cli.py::test_export_and_output_exists PASSED [ 17%]
test_cli.py::test_missing_file_error PASSED [ 18%]
test_cli.py::test_search PASSED [ 19%]
test_convert.py::test_genbank_round_trip PASSED [ 20%]
test_convert.py::test_genbank_locations_extract_same_bases PASSED [ 22%]
test_convert.py::test_codon_start_for_frame_two PASSED [ 23%]
test_convert.py::test_fasta PASSED [ 24%]
test_convert.py::test_protein_genbank PASSED [ 25%]
test_convert.py::test_output_exists PASSED [ 27%]
test_convert.py::test_output_is_input PASSED [ 28%]
test_convert.py::test_bad_output_folder PASSED [ 29%]
test_convert.py::test_genbank_date[2022.9.2-02-SEP-2022] PASSED [ 30%]
test_convert.py::test_genbank_date[2012.12.31-31-DEC-2012] PASSED [ 32%]
test_convert.py::test_genbank_date[2022.13.1-None] PASSED [ 33%]
test_convert.py::test_genbank_date[-None] PASSED [ 34%]
test_convert.py::test_genbank_date[None-None] PASSED [ 35%]
test_dnafile.py::test_sequence_topology_and_flags PASSED [ 37%]
test_dnafile.py::test_linear_single_strand PASSED [ 38%]
test_dnafile.py::test_features_segments_qualifiers_and_frame PASSED [ 39%]
test_dnafile.py::test_primers_one_based_and_simplified_dropped PASSED [ 40%]
test_dnafile.py::test_notes PASSED [ 41%]
test_dnafile.py::test_protein_and_rna PASSED [ 43%]
test_dnafile.py::test_not_a_snapgene_file PASSED [ 44%]
test_dnafile.py::test_cut_short_file PASSED [ 45%]
test_dnafile.py::test_unsupported_type PASSED [ 46%]
test_dnafile.py::test_no_sequence_packet PASSED [ 48%]
test_dnafile.py::test_missing_file PASSED [ 49%]
test_dnafile.py::test_find_feature PASSED [ 50%]
test_enzymes.py::test_position_is_last_base_before_cut PASSED [ 51%]
test_enzymes.py::test_site_across_origin PASSED [ 53%]
test_enzymes.py::test_named_enzyme_that_does_not_cut PASSED [ 54%]
test_enzymes.py::test_unknown_enzyme PASSED [ 55%]
test_enzymes.py::test_default_lists_unique_cutters_only PASSED [ 56%]
test_enzymes.py::test_protein_error PASSED [ 58%]
test_full_e2e.py::test_snapgene_ships_sample_files PASSED [ 59%]
test_full_e2e.py::test_every_shipped_file_matches_biopython_reader PASSED [ 60%]
test_full_e2e.py::test_every_cds_matches_snapgene_saved_translation PASSED [ 61%]
test_full_e2e.py::test_puc19_cut_sites_match_snapgene_map PASSED [ 62%]
test_full_e2e.py::test_puc19_genbank_export_keeps_ampr PASSED [ 64%]
test_full_e2e.py::test_alignment_with_snapgene_aligners[clustalo] PASSED [ 65%]
test_full_e2e.py::test_alignment_with_snapgene_aligners[muscle] PASSED [ 66%]
test_full_e2e.py::TestCLISubprocess::test_help PASSED [ 67%]
test_full_e2e.py::TestCLISubprocess::test_status_json PASSED [ 69%]
test_full_e2e.py::TestCLISubprocess::test_info_json PASSED [ 70%]
test_full_e2e.py::TestCLISubprocess::test_export_align_search_workflow PASSED [ 71%]
test_full_e2e.py::TestCLISubprocess::test_error_json PASSED [ 72%]
test_full_e2e.py::test_open_exported_genbank_in_snapgene SKIPPED [ 74%]
test_seqtools.py::test_subseq_linear PASSED [ 75%]
test_seqtools.py::test_subseq_circular_crosses_origin PASSED [ 76%]
test_seqtools.py::test_subseq_out_of_range[0-3] PASSED [ 77%]
test_seqtools.py::test_subseq_out_of_range[1-11] PASSED [ 79%]
test_seqtools.py::test_subseq_out_of_range[11-11] PASSED [ 80%]
test_seqtools.py::test_feature_sequence_minus_and_origin PASSED [ 81%]
test_seqtools.py::test_coding_sequence_frame_two PASSED [ 82%]
test_seqtools.py::test_translate_drops_partial_codon PASSED [ 83%]
test_seqtools.py::test_snapgene_translation_normalises PASSED [ 85%]
test_seqtools.py::test_find_motif_both_strands PASSED [ 86%]
test_seqtools.py::test_find_motif_palindrome_once PASSED [ 87%]
test_seqtools.py::test_find_motif_iupac PASSED [ 88%]
test_seqtools.py::test_find_motif_across_origin PASSED [ 90%]
test_seqtools.py::test_find_motif_protein PASSED [ 91%]
test_seqtools.py::test_bad_motif[ACGZ-dna] PASSED [ 92%]
test_seqtools.py::test_bad_motif[-dna] PASSED [ 93%]
test_seqtools.py::test_bad_motif[K1-protein] PASSED [ 95%]
test_seqtools.py::test_orfs_plus_strand PASSED [ 96%]
test_seqtools.py::test_orfs_minus_strand_range_on_top_strand PASSED [ 97%]
test_seqtools.py::test_orfs_across_origin PASSED [ 98%]
test_seqtools.py::test_orfs_protein_error PASSED [100%]
======================== 80 passed, 1 skipped in 1.34s =========================
```

Summary: 80 passed, 1 skipped (the `gui` test, off by default), 1.3 s.
With `SNAPGENE_GUI=1` the `gui` test also passed: 81 passed. It opened a GenBank export of
pUC19 in SnapGene.

Extra check outside the suite (same day): 413 `.dna` files found on this Mac (SnapGene
samples plus lab plasmids) load, and all 2064 CDS features with a saved translation match it.

### Coverage notes

- The manual check (features of an exported GenBank shown correctly on the SnapGene map) is
  pending: the file is open in SnapGene for review.
- `.rna` and `.prot` files are covered by synthetic tests only; the shipped samples tested
  end to end are `.dna`.
- `open` is checked only by "SnapGene is running afterwards"; the app gives no way to read
  back what it shows.

## Part 2b: results for cloning, editing and primer design

Run on 2026-10-30, macOS, SnapGene 8.2.3 (Viewer, no licence), Python 3.14,
Biopython 1.88, Primer3 2.4.0, installed command (`CLI_ANYTHING_FORCE_INSTALLED=1`).

```
test_assemble.py::test_feature_inside_a_piece_moves_and_keeps_its_qualifiers PASSED
test_assemble.py::test_feature_cut_in_half_is_dropped_and_reported PASSED
test_assemble.py::test_feature_that_stayed_behind_is_not_reported PASSED
test_assemble.py::test_flipped_piece_turns_the_strand_round_and_keeps_the_protein PASSED
test_assemble.py::test_feature_across_the_origin PASSED
test_assemble.py::test_piece_longer_than_the_file_keeps_the_first_copy PASSED
test_assemble.py::test_label_marks_new_bases PASSED
test_assemble.py::test_relocate_primers_keeps_the_ones_that_still_bind PASSED
test_cutting.py::test_five_prime_overhang PASSED
test_cutting.py::test_three_prime_overhang PASSED
test_cutting.py::test_blunt PASSED
test_cutting.py::test_enzyme_that_cuts_outside_its_site_restores_nothing PASSED
test_cutting.py::test_site_across_the_origin PASSED
test_cutting.py::test_double_digest_pieces PASSED
test_cutting.py::test_linear_file_keeps_its_own_ends PASSED
test_cutting.py::test_unknown_enzyme_and_protein PASSED
test_edit.py::test_put_bases_in PASSED
test_edit.py::test_put_bases_in_at_the_very_start_and_end PASSED
test_edit.py::test_delete_a_part PASSED
test_edit.py::test_swap_a_part PASSED
test_edit.py::test_delete_across_the_origin PASSED
test_edit.py::test_primer_follows_the_sequence PASSED
test_edit.py::test_protein_check_sees_a_frame_shift PASSED
test_edit.py::test_annotate_adds_one_feature PASSED
test_edit.py::test_annotate_checks_the_range PASSED
test_edit.py::test_bad_bases_and_bad_ranges PASSED
test_edit.py::test_linear_file_cannot_wrap PASSED
test_gblock.py::test_a_clean_fragment_has_no_warnings PASSED
test_gblock.py::test_too_short_and_too_long PASSED
test_gblock.py::test_gc_outside_the_range PASSED
test_gblock.py::test_run_of_six_g_is_flagged PASSED
test_gblock.py::test_short_gc_run_and_long_at_run PASSED
test_gblock.py::test_repeated_stretches PASSED
test_gblock.py::test_report_shape PASSED
test_infusion.py::test_primer_tails_match_the_takara_manual[A 5' overhangs] PASSED
test_infusion.py::test_primer_tails_match_the_takara_manual[B blunt ends] PASSED
test_infusion.py::test_primer_tails_match_the_takara_manual[C 3' overhangs] PASSED
test_infusion.py::test_the_fifteen_bases_come_from_the_vector_end PASSED
test_infusion.py::test_three_prime_overhang_bases_are_left_out PASSED
test_infusion.py::test_product_keeps_the_backbone_and_rebuilds_both_sites PASSED
test_infusion.py::test_one_enzyme_gives_vector_plus_insert_plus_one_site PASSED
test_infusion.py::test_no_restore_sites_leaves_the_site_out PASSED
test_infusion.py::test_no_restore_sites_on_one_cut_warns_about_repeated_bases PASSED
test_infusion.py::test_open_at_a_point PASSED
test_infusion.py::test_open_by_removing_a_part PASSED
test_infusion.py::test_two_cuts_keep_the_larger_piece_unless_told_otherwise PASSED
test_infusion.py::test_insert_from_a_file_brings_its_features PASSED
test_infusion.py::test_reversed_insert_flips_its_features PASSED
test_infusion.py::test_product_keeps_the_vectors_numbering[kwargs0] PASSED
test_infusion.py::test_product_keeps_the_vectors_numbering[kwargs1] PASSED
test_infusion.py::test_product_keeps_the_vectors_numbering[kwargs2] PASSED
test_infusion.py::test_product_keeps_the_vectors_numbering[kwargs3] PASSED
test_infusion.py::test_fragment_is_the_pcr_product PASSED
test_infusion.py::test_insert_frame_is_checked PASSED
test_infusion.py::test_overlap_outside_takaras_range_still_works_but_warns PASSED
test_infusion.py::test_open_vector_errors[kwargs0-too_many_cuts] PASSED
test_infusion.py::test_open_vector_errors[kwargs1-no_cut] PASSED
test_infusion.py::test_open_vector_errors[kwargs2-unknown_enzyme] PASSED
test_infusion.py::test_open_vector_errors[kwargs3-bad_range] PASSED
test_infusion.py::test_open_vector_errors[kwargs4-bad_range] PASSED
test_infusion.py::test_open_vector_errors[kwargs5-bad_request] PASSED
test_infusion.py::test_linear_vector_is_refused PASSED
test_infusion.py::test_short_insert_and_silly_overlap_are_refused PASSED
test_infusion.py::test_product_sites_can_be_counted_on_the_real_enzymes PASSED
test_primers.py::test_gc_and_tm PASSED
test_primers.py::test_longest_run_and_three_prime_gc PASSED
test_primers.py::test_hairpin_and_dimer_energies PASSED
test_primers.py::test_anchor_report PASSED
test_primers.py::test_choose_anchors_keeps_both_in_takaras_window PASSED
test_primers.py::test_choose_anchors_uses_what_it_has_on_a_short_insert PASSED
test_primers.py::test_warnings_fire_on_a_bad_primer PASSED
test_primers.py::test_pair_warnings_on_a_wide_tm_gap PASSED
test_cli.py::test_cut_json_and_human PASSED
test_cli.py::test_infusion_json PASSED
test_cli.py::test_infusion_will_not_overwrite PASSED
test_cli.py::test_infusion_needs_an_insert PASSED
test_cli.py::test_gblock_json PASSED
test_cli.py::test_edit_json PASSED
test_cli.py::test_annotate_json PASSED
test_full_e2e.py::test_puc19_double_digest_matches_snapgene_map PASSED
test_full_e2e.py::test_infusion_gfp_into_puc19 PASSED
test_full_e2e.py::test_infusion_product_reads_back_as_genbank PASSED
test_full_e2e.py::TestCLISubprocess::test_infusion_workflow PASSED
======================== 163 passed, 1 skipped in 2.06s ========================
```

Summary: the whole suite is 163 passed, 1 skipped (the `gui` test, off by
default), 2.1 s. 83 of those are the new tests listed above.

The three `test_primer_tails_match_the_takara_manual` cases are the important
ones: the forward and reverse 5' tails come out base for base the same as the
six primers printed in Figure 3 of Takara's manual, for 5' overhangs (SalI and
HindIII), blunt ends (SmaI) and 3' overhangs (KpnI).

### Checks done by hand on lab files, outside the suite

- mCherry (CMV_FKBP-LTKCter-mCherry, bases 2127-2834, stop codon left out) put in
  place of EGFP in pENTR1A-FKBP-GFP-CterLTK with `--remove 834-1550`. Product
  4507 bp, numbered like the vector, 11 features. FKBP to LTK reads in one frame
  with one stop, at the end: a 745 aa fusion protein.
- The same insert taken with its stop codon gave the "insert ends with the stop
  codon TAA" warning, and the fusion then stopped at mCherry. That is the warning
  doing its job.
- An insert range that crossed two features was reported as cutting mCherry and
  LTK C'ter in half, which caught an error in the range by hand.
- GFP and mCherry into pUC19: the backbone stays whole, the insert appears once,
  and both enzyme sites come back exactly once each when read as a circle.

### Coverage notes

- Primer3's Tm, not SnapGene's, so numbers can differ by about a degree. The
  hairpin and dimer limits (-5 and -12 kcal/mol) are rules of thumb, not Takara
  numbers, and no test fixes their exact value.
- IDT's gBlocks limits are checked against IDT's published numbers. IDT says
  acceptance is "complex and multifactorial", so a clean report is not a promise
  and no test can prove one.
- The frame check is simple on purpose: it looks at the insert's own length and
  last codon. It does not work out the frame of a vector CDS that runs into the
  insert, so `--at` and `--remove` designs still need the user's eye on the frame.
- Not covered, because not built: Gateway BP/LR, Golden Gate, plain ligation,
  more than one insert at a time, sequencing primer sets, qPCR probe design.

### Manual check

Open a product in SnapGene (`cli-anything-snapgene open PRODUCT.gb`) and confirm
the map shows the insert, the rebuilt sites and the two In-Fusion primers.

Done on 2026-10-30 with the pENTR1A-FKBP-mCherry-CterLTK product above. SnapGene
draws a 4507 bp circular map and the user checked it. It holds rrnB T1 (103-189)
and T2, attL1, FKBP, mCherry (834-1541), C'Ter LTK (1581-2714), attL2, KanR and
ori, in the parent vector's own numbering. EGFP is gone. The two primers are
drawn across the joins (mCherry In-Fusion FOR at 819-853, REV at the complement
of 1521-1556), and the vector bases on each side are whole: the AgeI site at 812
and the EagI site at 1544 both survive the design and are still unique.

## Part 3: PCR primer design (`pcr`, `primer-check`)

### Plan

Primer3 does the design, so the tests check the parts around it, which is where
mistakes would hide:

- the window handed to Primer3 holds the whole target, and wraps the origin on a
  circular file, and is clipped on a linear one
- every primer Primer3 returns is put back into the file's own numbering. The
  test cuts each primer out of the file at the reported place and compares it
  with the primer Primer3 gave, forward strand and reverse strand
- the target stays whole: the forward primer ends before it, the reverse primer
  starts after it
- product size limits, a Tm window nothing fits (reported, not crashed), an
  `--opt-tm` outside its own window, a protein file, a bad range, and a file too
  short for primers
- `primer-check` finds a site on either strand, counts the origin, and warns when
  the primer matches nowhere or more than once

### Results

Same run as Part 2b (2026-10-30, Primer3 2.4.0):

```
test_pcr.py::test_window_keeps_the_target_whole PASSED
test_pcr.py::test_window_wraps_the_origin PASSED
test_pcr.py::test_window_on_a_linear_file_is_clipped PASSED
test_pcr.py::test_design_finds_pairs_that_flank_the_target PASSED
test_pcr.py::test_design_product_size_limits PASSED
test_pcr.py::test_design_reports_when_nothing_fits PASSED
test_pcr.py::test_design_refuses_an_opt_tm_outside_the_window PASSED
test_pcr.py::test_design_errors PASSED
test_pcr.py::test_check_primer_finds_the_site PASSED
test_pcr.py::test_check_primer_on_the_minus_strand PASSED
test_pcr.py::test_check_primer_warns_when_it_does_not_bind_or_binds_twice PASSED
test_pcr.py::test_check_primer_refuses_nothing PASSED
test_cli.py::test_pcr_json PASSED
test_cli.py::test_primer_check_json PASSED
```

The whole suite is now **177 passed, 1 skipped** (the `gui` test, off by
default), 2.1 s.

### Coverage notes

- Primer3 itself is not re-tested; it is the engine most primer web tools use.
  The numbers are Primer3's, with its default salt and primer concentrations, so
  they can differ from SnapGene's by about a degree.
- `pcr` designs one pair at a time for one target. Sequencing primer sets and
  qPCR probe design are not built.

## Part 4: Gateway LR (`gateway`)

### Ground truth

Invitrogen's att sites, taken from the plasmids SnapGene ships rather than from
memory. A scan of the 203 bundled `.dna` files gives every att feature and its
sequence:

| Site | Length | Files | Sequence, 5' to 3' along its own strand |
|---|---|---|---|
| attB1 | 25 | 8 | `ACAAGTTTGTACAAAAAAGCAGGCT` |
| attB2 | 25 | 8 | `ACCACTTTGTACAAGAAAGCTGGGT` |
| attL1 | 100 | 4 | `CAAATAATG...CCAACTTTGTACAAAAAAGCAGGCT` |
| attL2 | 100 | 3 | `CAAATAATG...CCAACTTTGTACAAGAAAGCTGGGT` |
| attR1 | 125 | 78 | `ACAAGTTTGTACAAAAAAGCTGAACGAG...CAGTCACTATG` |
| attR2 | 125 | 79 | `ACCACTTTGTACAAGAAAGCTGAACGAG...CAGTCACTATG` |

attB1 is the first 20 bases of attR1 followed by the last 5 of attL1, and attB2
is the same arrangement of attR2 and attL2. That is the whole arithmetic. The
shared core is 15 bases in both pairs, but the code finds it by comparison
instead of trusting those offsets, because SnapGene annotates attR3 and attR4 as
124 bp, one base shorter than attR1 and attR2.

### Test inventory

| File | What it covers |
|---|---|
| `test_gateway.py` | the rule, reading the two files, the product, the warnings |
| `test_cli.py` | the `gateway` command, JSON and human output, one error |
| `test_full_e2e.py` | every bundled destination vector, pDEST14 in detail, the CLI |

### Unit test plan

- the rule: attB1 and attB2 come out exactly as SnapGene annotates them; attB is
  the attR head plus the attL tail; the core is in both sites; two sites that
  share no core are refused; attL1 does not recombine with attR2
- reading a file: the pair is found with the plus-strand site first; a file with
  no attL pair, two sites on one strand, a linear file and a protein file are
  each refused with their own code
- the product: length is backbone + 25 + insert + 25; the insert appears once;
  the ccdB cassette is gone; the destination's numbering is kept; both attB
  sites are annotated, attB1 on the plus strand and attB2 on the minus; the
  insert's features and the backbone's tags come across; an entry clone drawn
  the other way round still gives a forward insert
- the warnings: a vector with no ccdB between its attR sites; a tag in frame;
  a tag out of frame; an insert whose length is not a multiple of 3; an insert
  that still carries its stop codon

### End-to-end plan

- run one entry clone into all 77 Gateway destination vectors SnapGene ships
- pDEST14 in detail, checked against the file itself: backbone whole, cassette
  gone, both attB sites present once, AmpR and GFP proteins unchanged
- the product written and read back with Biopython
- the installed command through a subprocess, with `--json`

### Results

Run on 2026-10-30, same machine and versions as Part 2b.

```
test_gateway.py::test_attb1_and_attb2_match_snapgenes_own_annotation PASSED
test_gateway.py::test_attb_is_the_attr_head_and_the_attl_tail PASSED
test_gateway.py::test_the_shared_core_is_found_in_both_sites PASSED
test_gateway.py::test_sites_that_share_no_core_are_refused PASSED
test_gateway.py::test_att1_does_not_recombine_with_att2 PASSED
test_gateway.py::test_read_clone_finds_the_pair PASSED
test_gateway.py::test_a_file_without_two_attl_is_refused PASSED
test_gateway.py::test_two_sites_on_the_same_strand_are_refused PASSED
test_gateway.py::test_linear_and_protein_files_are_refused PASSED
test_gateway.py::test_lr_builds_the_expression_clone PASSED
test_gateway.py::test_the_ccdb_cassette_is_gone PASSED
test_gateway.py::test_the_product_keeps_the_destination_numbering PASSED
test_gateway.py::test_the_attb_sites_are_annotated_on_the_right_strands PASSED
test_gateway.py::test_the_insert_features_come_across PASSED
test_gateway.py::test_an_entry_clone_drawn_backwards_still_gives_a_forward_insert PASSED
test_gateway.py::test_a_vector_without_ccdb_is_flagged PASSED
test_gateway.py::test_tags_in_frame_are_reported PASSED
test_gateway.py::test_tags_out_of_frame_are_reported PASSED
test_gateway.py::test_an_insert_that_is_not_a_multiple_of_three_is_flagged PASSED
test_gateway.py::test_an_insert_that_keeps_its_stop_codon_is_flagged PASSED
test_cli.py::test_gateway_json PASSED
test_cli.py::test_gateway_human_and_errors PASSED
test_full_e2e.py::test_gateway_lr_runs_on_every_bundled_destination_vector PASSED
test_full_e2e.py::test_gateway_product_is_a_clean_expression_clone PASSED
test_full_e2e.py::test_gateway_product_reads_back_as_genbank PASSED
test_full_e2e.py::TestCLISubprocess::test_gateway_workflow PASSED
```

The sweep prints:

```
74 destination vectors recombined; 3 refused for att numbers that do not pair
(pDEST R4-R3 Vector II.dna, pDEST R4-R3.dna, pLenti6.4 R4R2 V5-DEST.dna)
```

Every one of the 74 gave exactly `ACAAGTTTGTACAAAAAAGCAGGCT` and
`ACCACTTTGTACAAGAAAGCTGGGT`. The 3 refusals are correct: those vectors carry
attR4 and attR3, which do not pair with an attL1/attL2 entry clone.

The whole suite is now **203 passed, 1 skipped** (the `gui` test, off by
default), 2.6 s.

### Checked by hand on a lab file

`pcDNA-DEST40` x `pENTR1A-FKBP-GFP-CterLTK`: 1705 bp taken out (CmR, ccdB, lac
UV5 promoter), 2287 bp insert, product 7775 bp with 19 features, attB1 at
911-935 and attB2 at 3223-3247. The tool reported that the insert is 2287 bp,
not a multiple of 3, and that the vector's V5 tag 50 bp downstream is therefore
out of frame with it by 2 bases. That is a real result, not a bug: pENTR1A puts
the insert out of frame with pcDNA-DEST40's C-terminal tag, so the fusion would
need a different entry clone or a different vector.

### Coverage notes

- The byproduct plasmid of the reaction (the cassette between two attP sites) is
  not built, because nobody keeps it.
- BP (attB x attP) is not built, so an entry clone cannot be made here.
- MultiSite Gateway with two or three inserts at once is not built. A two-site
  LR is all that is covered.
- The frame report looks 60 bp either side of the insert and names any CDS it
  finds. It does not work out what the fusion protein would be.
- Input must be `.dna`. GenBank is written but not read. Since Part 5 the
  product of a command is itself a `.dna` file, so it can be fed straight into
  the next command.


## Part 5: writing SnapGene files and the cloning history (`dnawrite`, `history`, `sgxml`)

Asked for after the Gateway work: SnapGene has a History view that draws how a
plasmid was cloned, and the question was whether products could carry one.

### Ground truth

SnapGene does not publish the history format, so it was taken from the files
SnapGene 8.2.3 ships and checked by opening written files in SnapGene itself.

- Packet `0x07` holds a `HistoryTree`: one `Node` for the file, with a `Node`
  per input nested under it. Twelve of the 203 bundled plasmids have one; the
  richest is `BlueScribe-mEGFP.dna`, 14 steps on nine levels.
- Operation names come from the binary: `inFusionCloning`, `gatewayLRCloning`,
  `amplifyFragment`, `insertFragment`, `goldenGateAssembly`,
  `primerDirectedMutagenesis`, `flip`, `newFileFromSelection`,
  `changeMethylation`, and `invalid` for a file not made in SnapGene.
- Packet `0x0B` holds a snapshot of one parent, promised by `resurrectable="1"`.
- The cookie of a written file is `SnapGene` + type 1 + versions 15 and 19, the
  values in the files SnapGene ships.

**The rule that matters**, found by writing test files and opening them:
SnapGene quits as it opens a file whose history step has input nodes but no
matching `InputSummary`. No dialog, no console message, no crash report: the
process is simply gone within two seconds. It was isolated by cutting a product
down one packet at a time (bases, features, notes and a single-node history all
opened fine) and then by trying a series of two-parent trees. Adding one
`InputSummary` per input fixed every one that had failed. `core.history.check` now
refuses such a tree before anything is written.

Two more rules came from the same files:

- `InputSummary` values with `name1`/`name2` are printed next to those names
  (`Replace BamHI (444) - HindIII (474)`, matching SnapGene's own
  `BlueScribe-mEGFP`). Values without names are 0-based and printed one higher.
- `resurrectable="1"` without a snapshot packet is safe once the mark is
  removed, which is what the writer does. A sample stripped of its `0x0B`
  packets opened fine.

### Test inventory

| File | Tests | What it covers |
|---|---|---|
| `test_dnawrite.py` | 21 | the round trip, rich text, colours, frames, methylation, what is refused |
| `test_history.py` | 17 | tree shape, the InputSummary rule, what In-Fusion and Gateway record |
| `test_cli.py` (added) | 6 | `.dna` by default, `.gb` on request, input files protected |
| `test_full_e2e.py` (added) | 5 | all 203 shipped files written and reread, and SnapGene opening one |

### Results

Run on 2026-11-01, macOS, SnapGene 8.2.3 (Viewer, no licence).

- **Round trip on everything SnapGene ships.** All 203 `.dna` files were
  written back out and read again: same bases, same topology, same feature
  names, ranges and strands, same primers. Biopython's independent SnapGene
  reader agrees on every one.
- **The real check.** `test_snapgene_opens_a_product_we_wrote` writes a Gateway
  product, opens it in SnapGene and asserts SnapGene is still running eight
  seconds later. It passes. Run it with `SNAPGENE_GUI=1`.
- **The History view.** Both lab products were opened by hand. The In-Fusion
  one draws the product over the vector and the PCR fragment, with the step
  labelled IN-FUSION CLONING, `Replace 834 .. 1550`, `Insert 1 .. 738`, and the
  PCR below it naming both primers and `Amplify 2127 .. 2834`. The Gateway one
  draws GATEWAY LR CLONING over pcDNA-DEST40 (ccdB and CmR visible on its map)
  and the entry clone, with `Replace attR1 (911) - attR2 (2615)` and
  `Insert attL1 (358) - attL2 (2844)`.
- **A parent's history is carried on.** `GFP Amplified.dna`, which SnapGene
  made by PCR, keeps that `amplifyFragment` step when it is cloned into pUC19,
  so the product's tree is four levels deep.
- **A broken parent history is dropped, not passed on.** A tree that would make
  SnapGene quit is left out of the product rather than copied into it.
- **Input files are protected.** A product may not be written over the vector,
  the insert, the entry clone or the destination vector, even with
  `--overwrite`.

The whole suite is now **251 passed, 2 skipped** (the two `gui` tests, off by
default), 3.3 s. With `SNAPGENE_GUI=1` the two gui tests pass as well.

### Checked by hand on lab files

Both products were rebuilt as `.dna` next to the lab plasmids and reread:

- `pENTR1A-FKBP-GFP-CterLTK-mCherry.dna`, 4507 bp, 9 features, 2 primers, four
  history steps.
- `pcDNA-DEST40-FKBP-GFP-CterLTK.dna`, 7775 bp, 19 features, three history
  steps. Same length, feature count and att site positions as the GenBank
  product built before Part 5, so the chemistry did not change.

### Coverage notes

- Snapshots of parent files (`0x0B`) are not written. The history draws
  without them; a parent just cannot be reopened out of the product.
- Enzyme sets, display settings, alignment and trace data are not written.
  SnapGene opens files without them.
- The saved `translation` qualifier is deliberately left out of written files:
  SnapGene works it out from the bases, and a copy could be stale after an edit.
- `edit` keeps the written story but drops the history tree, because the tree
  describes bases that are no longer there. A tree for an edit (SnapGene's own
  `insertFragment`) is not built.
- The history is checked for the one rule that makes SnapGene quit, not for
  everything SnapGene might dislike. A tree built by hand through the `history`
  module could still be wrong in some other way.
