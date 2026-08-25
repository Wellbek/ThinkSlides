# Vendored

`mermaid-11.4.1.min.js` is the unmodified UMD bundle from `mermaid@11.4.1` on npm.

Vendored rather than fetched from a CDN for three reasons.

A render must not make a network request.
A diagram is drawn while a customer's deck is being generated, and a page that reaches out to a third-party host mid-render is a page that fails in an air-gapped deployment and leaks a request in every other one.

A CDN call is the classic thing that passes locally and fails in the container.

And the version is pinned so layout is deterministic.
Mermaid decides where every node goes; a minor upgrade that improves its layout also silently changes every diagram this service has ever produced, which is a thing to do deliberately rather than on someone else's release schedule.

To upgrade: `npm pack mermaid@<version>`, take `package/dist/mermaid.min.js`, and re-record the fixtures in `services/ai/tests/fixtures/mermaid/` so the tests still describe what this version actually emits.
