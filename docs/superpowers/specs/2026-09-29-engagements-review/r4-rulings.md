# Round 4 rulings (design v4 -> v5)

Scores r4: Gemini 98 / 0.95 CONVERGE; Kimi 94 / 0.90 CONVERGE; Claude 88 / 0.86 CONVERGE; Codex 93 / 0.90 DISSENT.

| # | Finding (who) | Ruling | v5 change |
|---|---|---|---|
| 1 | On a laptop the agent shares the owner's UID, so owner/mode checks cannot protect binding.json; a17 path checks allow a direct Write (Codex P1, probe) | ACCEPT | Section 8: the gate's protected-record matchers cover binding.json and control/ for both kinds against agent writes, deletion, replacement and operations on containing directories; laptop regression tests before migration |
| 2 | Stage temp files in the destination folder; no shared staging folder exists (Claude) | ACCEPT | Section 4.1 publication text |
| 3 | Approver account must be in every engagement group to pass the 0750 root (Claude) | ACCEPT | Section 4.1 note; the admin host script adds it |
| 4 | AGENTS.md must point to state/CURRENT_WORK.md (Claude) | ACCEPT | Section 5 |
| 5 | Doctor should report claims that match no request (Kimi P2) | ACCEPT | Section 6 doctor line |
