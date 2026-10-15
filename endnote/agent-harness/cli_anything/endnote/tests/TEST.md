# cli-anything-endnote: tests

## How to run

See `../README.md`, section Tests. Switches: `PUBMED_NETWORK=1`, `ENDNOTE_E2E=1`
(only `EndNoteCLI_Test.enl` open), `ENDNOTE_REAL_DOCX=<path>`.

## Coverage

| File | What it checks |
|---|---|
| `test_word_link.py` | Marker grammar; replacement in one run, across runs, across proofing and bookmark elements, in hyperlinks, tables, text boxes, tracked insertions; deleted text ignored; unresolved markers left yellow; output ZIP parts kept; input unchanged; a real grant document (copy). |
| `test_pubmed.py` | Parsing (authors, collective authors, MedlineDate, structured abstract, DOI, PMCID, MeSH); book records ignored; batching at 200; network and XML errors; one live PubMed record. |
| `test_enxml.py` | EndNote XML layout matches a real export; escaping; non-ASCII; line breaks as `&#xD;`. |
| `test_backend.py` | osascript arguments and UTF-8; lookup parsing; missing app, timeout, Automation denied, other errors. |
| `test_library.py` | Single-library rule; skip existing; exact PMID match; duplicates; failed imports; chunks of 25; status. |
| `test_cli.py` | Every command, human and JSON output, exit codes, link safety rules, REPL default. |
| `test_docs.py` | Skill lists every command and error code; repo copy identical; no em-dashes. |
| `test_full_e2e.py` | Installed command and `python -m`; real import into the test library; add twice; link end to end with real record numbers. |

## Manual Word check

2026-10-15, done by Urko on macOS with Microsoft Word and EndNote 2025, with only `EndNoteCLI_Test.enl` open.
Input: `~/EndNoteCLI_Test_demo_LINKED.docx`, made by `cli-anything-endnote link`, with the text
`Lung cancer deaths {Siegel, 2026 #13}. EML4-ALK {Soda, 2007 #14; Güthe, 2004 #15}.`
After EndNote > Update Citations and Bibliography, Word showed:

```
Lung cancer deaths (1). EML4-ALK (2, 3).

1. R. L. Siegel, T. B. Kratzer, N. S. Wagle, H. Sung, A. Jemal, Cancer statistics, 2026. CA Cancer J Clin 76, e70043 (2026).
2. M. Soda et al., Identification of the transforming EML4-ALK fusion gene in non-small-cell lung cancer. Nature 448, 561–566 (2007).
3. S. Güthe et al., Very fast folding and association of a trimerization domain from bacteriophage T4 fibritin. J Mol Biol 337, 905–915 (2004).
```

Result: PASS. All temporary citations became live EndNote citations, and the bibliography lists the 3 papers imported by the CLI.

## Last full run

Run on 2026-10-15 with `ENDNOTE_E2E=1`, `PUBMED_NETWORK=1` and `ENDNOTE_REAL_DOCX` set, only `EndNoteCLI_Test.enl` open.

```
test_backend.py::test_parse_lookup_output_reads_every_row PASSED [  1%]
test_backend.py::test_parse_lookup_output_empty PASSED [  2%]
test_backend.py::test_parse_lookup_output_rejects_unknown_record_id PASSED [  3%]
test_backend.py::test_lookup_with_no_pmids_does_not_call_endnote PASSED [  4%]
test_backend.py::test_missing_app_raises_endnote_missing PASSED [  5%]
test_backend.py::test_timeout_raises_endnote_timeout PASSED [  6%]
test_backend.py::test_automation_denied_is_recognised PASSED [  7%]
test_backend.py::test_other_errors_are_applescript_errors PASSED [  8%]
test_backend.py::test_arguments_and_unicode_are_passed_to_osascript PASSED [  9%]
test_backend.py::test_import_sends_xml_as_one_argument PASSED [ 10%]
test_backend.py::test_open_libraries_parses_names PASSED [ 11%]
test_cli.py::test_version PASSED [ 12%]
test_cli.py::test_no_command_starts_repl PASSED [ 13%]
test_cli.py::test_status_json_with_global_or_local_flag PASSED [ 14%]
test_cli.py::test_status_human_when_not_running PASSED [ 15%]
test_cli.py::test_add_reports_added_skipped_failed PASSED [ 16%]
test_cli.py::test_add_reads_file_and_ignores_comment_lines PASSED [ 17%]
test_cli.py::test_add_exits_1_when_every_pmid_fails PASSED [ 18%]
test_cli.py::test_add_without_pmids_is_an_error PASSED [ 19%]
test_cli.py::test_library_errors_become_json_errors PASSED [ 20%]
test_cli.py::test_find_shows_not_found PASSED [ 21%]
test_cli.py::test_link_writes_linked_copy PASSED [ 22%]
test_cli.py::test_link_human_output_tells_the_next_step PASSED [ 23%]
test_cli.py::test_link_refuses_existing_output_before_touching_endnote PASSED [ 24%]
test_cli.py::test_link_overwrite_replaces_output PASSED [ 25%]
test_cli.py::test_link_output_cannot_be_the_input PASSED [ 26%]
test_cli.py::test_link_without_markers_writes_nothing PASSED [ 27%]
test_cli.py::test_link_warns_about_tracked_changes PASSED [ 28%]
test_cli.py::test_link_when_every_pmid_fails_writes_nothing_and_exits_1 PASSED [ 29%]
test_cli.py::test_link_bad_docx PASSED [ 30%]
test_docs.py::test_packaged_skill_covers_every_command_and_error_code PASSED [ 31%]
test_docs.py::test_repo_root_skill_is_identical_to_packaged_copy PASSED [ 32%]
test_docs.py::test_readme_exists_with_install_and_test_sections PASSED [ 33%]
test_docs.py::test_docs_have_no_em_dashes PASSED [ 34%]
test_enxml.py::test_record_follows_endnote_export_layout PASSED [ 35%]
test_enxml.py::test_non_ascii_and_line_breaks_survive PASSED [ 36%]
test_enxml.py::test_special_characters_are_escaped_and_control_characters_dropped PASSED [ 37%]
test_enxml.py::test_two_records_in_one_document PASSED [ 38%]
test_full_e2e.py::test_import_round_trip PASSED [ 39%]
test_full_e2e.py::test_delete_helper_refuses_when_other_libraries_are_open PASSED [ 40%]
test_full_e2e.py::test_installed_command_help_lists_commands PASSED [ 41%]
test_full_e2e.py::test_installed_command_version PASSED [ 42%]
test_full_e2e.py::test_python_m_entry_point PASSED [ 43%]
test_full_e2e.py::test_installed_command_reports_bad_docx_as_json PASSED [ 44%]
test_full_e2e.py::test_installed_command_status PASSED [ 45%]
test_full_e2e.py::test_add_twice_then_find PASSED [ 46%]
test_full_e2e.py::test_link_command_end_to_end PASSED [ 47%]
test_library.py::test_collect_pmids_dedupes_and_skips_comment_lines PASSED [ 48%]
test_library.py::test_require_single_library[None-no_library] PASSED [ 49%]
test_library.py::test_require_single_library[libraries1-no_library] PASSED [ 50%]
test_library.py::test_require_single_library[libraries2-many_libraries] PASSED [ 51%]
test_library.py::test_error_messages_match_the_spec PASSED [ 52%]
test_library.py::test_add_skips_existing_adds_new_and_reports_unknown PASSED [ 53%]
test_library.py::test_add_twice_adds_nothing_the_second_time PASSED [ 54%]
test_library.py::test_substring_hit_is_not_a_match PASSED [ 55%]
test_library.py::test_duplicate_pmid_uses_lowest_record_and_warns PASSED [ 56%]
test_library.py::test_import_that_does_not_appear_is_a_failure PASSED [ 57%]
test_library.py::test_imports_are_sent_in_chunks_of_25 PASSED [ 58%]
test_library.py::test_add_checks_the_library_before_anything_else PASSED [ 59%]
test_library.py::test_status PASSED [ 60%]
test_library.py::test_find_returns_none_for_missing PASSED [ 61%]
test_pubmed.py::test_parse_normal_article PASSED [ 62%]
test_pubmed.py::test_parse_collective_author_structured_abstract_medline_date PASSED [ 63%]
test_pubmed.py::test_book_records_are_ignored PASSED [ 64%]
test_pubmed.py::test_fetch_reports_missing_pmids_and_keeps_input_order PASSED [ 65%]
test_pubmed.py::test_fetch_splits_into_batches_of_200 PASSED [ 66%]
test_pubmed.py::test_fetch_with_no_pmids_makes_no_request PASSED [ 67%]
test_pubmed.py::test_unparseable_answer_raises_pubmed_error PASSED [ 68%]
test_pubmed.py::test_network_error_raises_pubmed_error PASSED [ 69%]
test_pubmed.py::test_dropped_connection_while_reading_raises_pubmed_error[failure0] PASSED [ 70%]
test_pubmed.py::test_dropped_connection_while_reading_raises_pubmed_error[failure1] PASSED [ 71%]
test_pubmed.py::test_connection_reset_on_open_raises_pubmed_error PASSED [ 72%]
test_pubmed.py::test_real_pubmed_soda_2007 PASSED [ 73%]
test_word_link.py::test_marker_forms_match[[PMID 17625570]-expected0] PASSED [ 74%]
test_word_link.py::test_marker_forms_match[[PMID: 17625570]-expected1] PASSED [ 75%]
test_word_link.py::test_marker_forms_match[[PMIDs 17625570; 41528114]-expected2] PASSED [ 76%]
test_word_link.py::test_marker_forms_match[[pmid 17625570, 41528114]-expected3] PASSED [ 77%]
test_word_link.py::test_marker_forms_match[[ PMID 017625570 ]-expected4] PASSED [ 78%]
test_word_link.py::test_near_misses_do_not_match[[PMID]] PASSED [ 79%]
test_word_link.py::test_near_misses_do_not_match[PMID 17625570] PASSED [ 80%]
test_word_link.py::test_near_misses_do_not_match[[PMID abc]] PASSED [ 81%]
test_word_link.py::test_near_misses_do_not_match[(PMID 17625570)] PASSED [ 82%]
test_word_link.py::test_near_misses_do_not_match[[DOI 10.1/x]] PASSED [ 83%]
test_word_link.py::test_merge_single_citation_is_unchanged PASSED [ 84%]
test_word_link.py::test_merge_keeps_order_and_uses_one_pair_of_braces PASSED [ 85%]
test_word_link.py::test_marker_in_one_run PASSED [ 86%]
test_word_link.py::test_marker_split_across_three_runs_keeps_outer_formatting PASSED [ 87%]
test_word_link.py::test_two_markers_in_one_paragraph PASSED [ 88%]
test_word_link.py::test_marker_inside_tracked_insertion_and_deleted_text_ignored PASSED [ 89%]
test_word_link.py::test_unresolved_marker_is_left_unchanged_and_yellow PASSED [ 90%]
test_word_link.py::test_marker_split_by_proofing_and_bookmark_elements PASSED [ 91%]
test_word_link.py::test_marker_inside_hyperlink_and_table_cell PASSED [ 92%]
test_word_link.py::test_marker_split_into_a_hyperlink_leaves_no_empty_wrapper PASSED [ 93%]
test_word_link.py::test_text_box_paragraph_is_processed_once PASSED [ 94%]
test_word_link.py::test_output_keeps_every_part_and_no_new_namespace_prefixes PASSED [ 95%]
test_word_link.py::test_input_file_is_not_modified PASSED [ 96%]
test_word_link.py::test_scan_lists_markers_texts_and_unique_pmids_in_order PASSED [ 97%]
test_word_link.py::test_not_a_docx_raises_bad_docx PASSED [ 98%]
test_word_link.py::test_real_document PASSED [100%]
============================== 99 passed in 5.76s ==============================
```
