import unittest

from database.database import _normalize_database_url


class DatabaseUrlTests(unittest.TestCase):
    def test_normalizes_render_postgres_url(self):
        self.assertEqual(
            _normalize_database_url("postgres://user:pass@host/db"),
            "postgresql+psycopg://user:pass@host/db",
        )

    def test_normalizes_postgresql_url(self):
        self.assertEqual(
            _normalize_database_url("postgresql://user:pass@host/db"),
            "postgresql+psycopg://user:pass@host/db",
        )

    def test_preserves_sqlite_url(self):
        self.assertEqual(
            _normalize_database_url("sqlite:///./travelagent.db"),
            "sqlite:///./travelagent.db",
        )


if __name__ == "__main__":
    unittest.main()
