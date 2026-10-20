import unittest

from infrastructure.database import WRITE_LOCK_KEY, PostgresConnection


class _RecordingCursor:
    def __init__(self, statements):
        self._statements = statements

    def execute(self, sql, params=()):
        self._statements.append((sql, tuple(params)))


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _RecordingCursor(self.statements)


class PostgresWriteLockTests(unittest.TestCase):
    def test_begin_immediate_takes_transaction_scoped_write_lock(self) -> None:
        connection = PostgresConnection.__new__(PostgresConnection)
        connection._connection = _RecordingConnection()

        connection.execute("BEGIN IMMEDIATE")

        self.assertEqual(
            connection._connection.statements,
            [
                ("SET LOCAL lock_timeout = '30s'", ()),
                ("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
