# Cover and external lyric metadata correction — October 16

The reported Lujon frame displayed NetEase JSON header rows containing writer
and composer credits as sung lyrics inside the album cover. Cover and external
lyrics already use the same `_display_lyrics()` preparation and reading layers;
there is no independent cover-only cleanup implementation to synchronize.

The shared source parser accepted an unrecognized JSON header as plain text.
When a response contained only those headers, alphabetic JSON keys passed its
vocal-content check and the parser synthesized lyric times. Display credit
matching could not recognize the production role hidden inside JSON. Existing
generated caches could therefore retain the same synthetic rows.

`api.lyrics.is_structured_lyric_metadata()` recognizes the observed NetEase
header schema: a finite numeric `t`, a nonempty `c` array and string `tx`
fragments. It does not classify arbitrary braced text or unrelated JSON as
metadata. The parser rejects these headers before plain-text synthesis and
when reading timestamp-wrapped cached rows. Genuine YRC lines, units, original
times and translations retain their established parsing path.

The shared display presenter reuses the same predicate, so already loaded
provider rows and caches are filtered in both layouts even when intro cleanup
is disabled. A metadata-only result uses the existing no-lyrics behavior.
No user cache deletion, network request, account mutation or layout-specific
lyric replacement is required by this correction.

Focused native verification passed 77 parser tests and 51 display/cover tests.
The latter verify identical surviving rows and original unit mapping across
cover/external layouts with translation enabled and disabled, plus cached
plain/timed metadata-only rendering. The saved display log is
`logs/oct16-structured-lyric-focused.log`; parser verification uses
`python -m unittest tests.test_lyrics_store -v` and is also part of full discovery.

Final native Windows discovery passed **5029 tests in 316.990 seconds**, with
no skips, Python tracebacks or Qt callback errors. The 51 display/cover tests
also passed at DPR 2 in 1.762 seconds. Logs are
`logs/oct16-style-footer-lyrics-full-verified.log` and
`logs/oct16-structured-lyric-focused-dpr2.log`. Compilation and
`git diff --check` passed. These are offline fixtures; they do not establish
live network delivery or physical display smoothness. Version remains 6.412.

The corrected source is included in the October 16 immersive/footer EXE,
installer and portable archive. Frozen acceptance and all 246 package checks
passed; archive CRC and SHA256 evidence are recorded in
[the final artifact report](installer-entry-collection-followup-2026-10-16.md#final-rebuilt-artifacts).
