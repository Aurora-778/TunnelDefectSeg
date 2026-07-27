"""Legacy checkpoints and opt-in Phase A3.1 canonical state primitives."""

from .store import (
    CHECKPOINT_KINDS,
    COMPLETION_EVIDENCE_SCHEMA_VERSION,
    STATE_JOURNAL_SCHEMA_VERSION,
    STATE_JOURNAL_TAIL_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    StateConflictError,
    StateRecoveryDeferredError,
    StateRecoveryRequiredError,
    StateStore,
    StateStoreError,
    load_checkpoint,
    make_state,
    save_checkpoint,
)

__all__ = [
    "CHECKPOINT_KINDS",
    "COMPLETION_EVIDENCE_SCHEMA_VERSION",
    "STATE_JOURNAL_SCHEMA_VERSION",
    "STATE_JOURNAL_TAIL_SCHEMA_VERSION",
    "STATE_SCHEMA_VERSION",
    "StateConflictError",
    "StateRecoveryDeferredError",
    "StateRecoveryRequiredError",
    "StateStore",
    "StateStoreError",
    "load_checkpoint",
    "make_state",
    "save_checkpoint",
]
