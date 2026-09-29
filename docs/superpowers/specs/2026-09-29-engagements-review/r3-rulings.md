# Round 3 rulings (design v3 -> v4)

Scores r3: Gemini 95 / 0.90 CONVERGE; Kimi 86 / 0.78 DISSENT; Claude 84 / 0.83 DISSENT; Codex 82 / 0.85 DISSENT.

| # | Finding (who) | Ruling | v4 change |
|---|---|---|---|
| 1 | Members cannot replace engagement.json / CURRENT_WORK.md under the 0750 root; sticky actions/ blocks closing a colleague's action (Claude, Kimi, Codex) | ACCEPT | New `state/` subfolder (admin:<group> 2770, setgid, no sticky) holds engagement.json, CURRENT_WORK.md and actions/; atomic replace under the engagement lock works for any member. Multi-UID tests: lifecycle change, regeneration, closing a colleague's action |
| 2 | Gate trusts a repository list members can edit (Codex, Kimi) | ACCEPT | Gate-authoritative data (engagement ID, kind, repository URLs) moves to `binding.json` in the admin-owned root (admin:<group> 0644, replaceable only by admin); state/engagement.json keeps member-editable metadata (title, owner, lifecycle). The gate verifies binding.json owner and mode |
| 3 | hidepid=2 breaks the presence check for connected launches (Codex, reproduced synthetically) | ACCEPT: drop hidepid | No hidepid; command lines carry only opaque engagement IDs on a shared host, so nothing sensitive is exposed |
| 4 | Claim consumption must be an exclusive create-only marker (Kimi) | ACCEPT | Documented: claims/<id>.consumed created with O_EXCL (matches approval.py) |
| 5 | Tier 1 signing key is private per home; workers cannot verify an approver's signature (Claude P2) | ACCEPT | On a shared host the approval mode is the owner check on control/ (Tier 2) |
| 6 | Admin-owned .claude/ blocks saving "always allow" (Claude P2) | ACCEPT | Per-person permission answers are saved at user scope (~/.claude); the engagement .claude/ holds only shared settings |
| 7 | A member can pre-create a claim marker to block a colleague (Claude P2) | NOTED | Low impact; watched in the pilot; claims carry the claimer's UID and doctor reports claims not matching a request |
