"""External reservation platform adapters.

Deliberately re-exports nothing. `backend.db.repositories.mock_booking` imports
`backend.integrations.base`, which initializes this package; eagerly importing
`backend.integrations.mock_booking` here therefore closed a cycle back into the
half-initialized repository module, and any module that reached the repository
before this package became un-importable on its own.

The full test suite hid it (alphabetically earlier tests import `backend.main`,
which initializes this package first), but `python -c "import
backend.workers.tasks.monitor_watch"` has always failed. Import the submodules
directly -- every caller already does.
"""
