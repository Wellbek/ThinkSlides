"""
The design layer: what a page *is*, separately from how it is painted.

``schema.py`` holds the DesignDoc - a page described as a grid, a tree of
frames, and content bound to those frames by role. Nothing in it carries a
coordinate that a model chose. Geometry comes from the Design Language Pack
the doc is set in, which is measured from a real publication rather than
invented, so "make it look like this report" is a resolvable reference and
not a style adjective.
"""
