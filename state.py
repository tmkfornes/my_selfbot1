"""Shared runtime flags. `paused` is set by `.خاموش` and cleared by `.روشن`."""

paused: bool = False
clock_was_on: bool = False  # remembers the clock state so `.روشن` can restore it
