"""Kinlight backend — pure, side-effect-free helpers extracted from main.py.

These modules contain logic that reads no database, encryption, network, or
logging globals, so they can be unit-tested and reasoned about in isolation.
They are re-exported from ``main`` to preserve the app's import surface.
"""
