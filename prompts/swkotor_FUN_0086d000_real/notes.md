# Notes

- Exported from Ghidra facts in `target/swkotor-match/facts/functions.jsonl`.
- Target bytes were wrapped in a COFF object only to make objdiff consume the function slice.
- Candidate source is ordinary C, not inline assembly and not a byte emitter.
- This is a single `.bind` loader function. Full `swkotor.exe` recovery still requires unpacking/decoding `.text` and matching every code function.
