# /process-meeting

Extract a recording timeline and turn supplied meeting material into decisions and delivery work.

**Interface:** Native CLI with conversational interpretation. A slash command is an assistant instruction, not a separate shell executable.

Resolve the supplied video and optional transcript, desired output and purpose.
Inspect `torque meeting --help` for available extraction/provider options. The native
baseline processes local video/transcript artifacts:

```sh
torque meeting --video <recording-path> --transcript <transcript-path> --output <private-output-directory>
```

Omit `--transcript` if absent. Check ffmpeg and optional image dependencies. Supported
sensitivity/frame-sampling controls can tune long recordings. Read `timeline.json`
and selected frame images, accounting for missing transcript coverage and extraction
limitations; frame sampling does not capture every moment or prove speaker intent.

Visual/model analysis is optional. The inherited native `--analyze` adapter calls
the Gemini CLI for selected frame images; it is not a generic provider selector.
Inspect its availability and actual destination before using it; do not add that
flag merely because extraction was requested. Other assistant/provider analysis
uses the environment's normal tools and authorized inputs separately. Missing or
failed optional analysis is distinct from successful local frame extraction.

Produce source/timestamp-backed decisions, requirements, questions, owners and next
actions suited to the user's request. Separate explicit decisions from inferred
interpretation. Save outputs privately and link them from discovery/session records.
Do not stop at “frames extracted” if the user asked for meeting analysis or a work plan.
