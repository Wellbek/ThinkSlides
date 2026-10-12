# Recorded Mermaid output

`flowchart.svg` is what `mermaid@11.4.1` actually emitted for

    graph LR
      A[Ingest] --> B[Compose]
      B --> C{Fits?}
      C -->|yes| D[Render]
      C -->|no| B

after `app/renderer/svg/mermaid.py` normalised it - the theme stamped onto the nodes, and the `<foreignObject>` labels lifted into real SVG text.

Recorded so the import path is testable on a machine with no browser.
A test that needs Chromium is a test that gets skipped in CI and then stops describing anything.

Re-record it whenever the pinned Mermaid version changes, so the tests keep describing what that version emits rather than what an older one did.

`sequence.svg` is what the same version emitted for

    sequenceDiagram
      participant C as Cache
      participant O as Origin
      C->>O: GET /page
      O-->>C: 200 + ETag
      Note over C: copy goes stale
      C->>O: If-None-Match
      O-->>C: 304 Not Modified

with the engineering-report palette, recorded for #350: its message lines are drawn `stroke="none"` and coloured by class, its arrowheads are `<marker>` definitions, and its note's border is an `hsl()` colour.
