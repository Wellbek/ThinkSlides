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
