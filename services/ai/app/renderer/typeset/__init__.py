"""
Typesetting: measuring text the way the renderer will draw it.

``fonts`` reads real advance widths out of the font file fontconfig resolves
- the same file LibreOffice will use - in place of the fixed 0.52 average
character width the previous estimator assumed. ``shape`` breaks lines
greedily on those widths, which is what both PowerPoint and LibreOffice do.
``policy`` holds the per-pack legibility floors that replace the global
``MIN_BODY_PT``.

Measured against 128 blocks rendered through the real LibreOffice path, the
character-count estimate got 49.2% of line counts exactly right; reading the
font is exact on all 128.
"""
