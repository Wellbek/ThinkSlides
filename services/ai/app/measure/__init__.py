"""
Measuring rendered output.

One measurement path, two consumers. The style-fidelity harness (``app.eval``)
uses it to score generated decks against the reference; template extraction
uses it to learn a template's density and type scale. Both need the same
answers to the same questions - how much is on this page, at what sizes, in
what colours - and two implementations of that would drift within a release.

Nothing here calls a model. Every number is arithmetic over a rendered page's
text spans, drawing paths, images and pixels, which is what makes a template's
measured density trustworthy rather than a model's impression of it.
"""
