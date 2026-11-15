"""jsc_memory: Torque's lessons loop (capture, store, find again at the point of use,
judge, review, promote). The package name is kept from the earlier toolkit.

- `lesson`    one lesson, one Markdown file with a small YAML front matter
- `storage`   scopes are folders (client, initiative, firm; the platform ships with the
              package); atomic writes, one lock, an events log, tolerance of bad files
- `scrubber`  what may not be saved: credentials in every scope, client names in firm scope
- `vocab`     words to tasks, metadata types and error matchers, for capture and recall
- `intake`    capture a lesson, save what a session taught, list a host's own memory
              files, import the earlier JSON store
- `recall`    match, rank and print lessons; `recall.for_command` is the function other
              packages call before a consequential step
- `feedback`  helpful, stale, verify, retire, restore, edit
- `review`    the review report, the counts, the doctor report and the self-check
- `promote`   a proposal for a wider or stronger form; client-to-firm is applied here
- `cli`       `torque lesson` (the `jsc-memory` script runs the same commands)

User guide: docs/lessons.md.
"""

__version__ = "2.0.0"
__schema_version__ = 2
