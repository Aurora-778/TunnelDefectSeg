# Phase C-4 Windows Control-Entry Exclusion Feasibility

## Decision

Phase C-4 does **not** enable a StateStore transition.  The Windows directory
oplock available to a user-mode process is not a mandatory exclusion for
sibling recovery/release control entries.  On a directory, adding, deleting,
replacing, renaming, or changing a child breaks an R/RH oplock to None, but the
break is advisory and the filesystem operation does not wait for an
acknowledgement.  Consequently there is still an uncloseable interval between
observing an unbroken oplock and opening a StateStore journal.

The isolated probe and native tests exist to preserve this negative result as
executable evidence.  They are not a production backend, are not exported,
and must not be connected to C-3.

Primary semantics reference:

- Microsoft `FSCTL_REQUEST_OPLOCK`: directory content changes break an R/RH
  oplock without requiring acknowledgement; only rename/delete of the
  directory itself can require acknowledgement for an RH-to-R break.
- Microsoft `Breaking Oplocks`: operations are held only for oplock breaks
  whose output requires acknowledgement.

## Acceptance criteria

1. Run on real Windows x64 and require a local NTFS directory for the native
   sibling-entry experiments.
2. Acquire a real asynchronous RH directory oplock with
   `FSCTL_REQUEST_OPLOCK`.
3. From a second process, prove that create, delete, replace, and rename of
   sibling control entries complete while the owner handle remains open and
   without an acknowledgement from the owner.
4. Prove that each operation signals an oplock break, but that notification is
   not a mandatory pre-journal exclusion.
5. Cover owner-handle close and owner-process crash.  Both release the kernel
   state and cannot preserve exclusion for recovery.
6. Reject junction/reparse roots and fail closed when Windows x64, NTFS, the
   asynchronous handle, event, or oplock grant is unavailable.
7. Run C-2/C-3 regression tests and statically prove the probe is absent from
   the production import graph.  C-3 must continue to return zero authority
   before StateStore recovery, journal open, or CAS.

## Scope

Allowed changes are limited to this design, one private Windows feasibility
probe, focused native tests, and a dedicated Windows CI workflow.  The probe
may use a path-based open because it is a negative experiment and never a
production capability.  That exception does not weaken the C-2/C-3
handle-relative traversal contract.

The following are zero-change boundaries:

- `orchestrator/state/` including schema and reducer
- `orchestrator/inspection_workflow/locking.py`
- all Resume modules
- Claim Policy and claim modules
- Manifest and Publication modules
- C-2 admission and Windows traversal production modules
- C-3 `review_commit.py`
- `orchestrator/inspection_workflow/__init__.py`

## Feasibility verdict

`FSCTL_REQUEST_OPLOCK` is useful as a change notification and can retain an
acknowledged break for rename/delete of the watched directory itself.  It
cannot retain sibling-entry creation, deletion, replacement, or rename.
Polling the break event immediately before a journal append would only replace
one check-then-use race with another.

Therefore Phase C-4 is **not feasible with the evaluated primitive**.  C-3
remains audit-only and its mandatory control-entry gate remains unavailable.
A future proposal must introduce a stronger kernel-enforced primitive and be
reviewed as a new design; it must not reinterpret this advisory oplock as a
lease.

## Native observation

On the development Windows x64 NTFS volume, the break output carried flag
`0x1`, while each second-process operation had already returned success and
its create/delete/replace/rename effect was visible before the owner closed
its handle or issued any acknowledgement.  The feasibility decision therefore
rests on the observable exclusion property, not on interpreting that flag as
a promise that the sibling mutation was held.
