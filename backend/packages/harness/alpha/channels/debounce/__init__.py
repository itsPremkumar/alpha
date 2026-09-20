"""Inbound message debouncer and turn batcher inspired by OpenClaw."""

from alpha.channels.debounce.debouncer import BatchedTurn, InboundDebouncer

__all__ = ["BatchedTurn", "InboundDebouncer"]
