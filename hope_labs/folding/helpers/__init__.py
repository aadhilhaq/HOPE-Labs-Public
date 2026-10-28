"""Helpers run by a tool's own Python rather than by the launcher's.

The same arrangement as hope_flow/helpers: neither folding tool agrees with the launcher about a
version of Python, so anything that has to import a tool's own code is a script handed to that
tool's interpreter and read back as JSON on stdout.
"""
