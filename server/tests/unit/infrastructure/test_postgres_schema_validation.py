import pytest

from mamemai_server.infrastructure.postgres import connect


def test_rejects_a_schema_name_with_a_space():
    """Spec: TR-5xx — validated before any connection attempt, since `schema` reaches
    libpq's `options` startup parameter rather than a bound query parameter."""
    with pytest.raises(ValueError):
        connect("postgresql://mamemai@localhost/mamemai", "alice; SET foo=bar")


def test_rejects_a_schema_name_starting_with_a_digit():
    with pytest.raises(ValueError):
        connect("postgresql://mamemai@localhost/mamemai", "1alice")


def test_rejects_an_uppercase_schema_name():
    with pytest.raises(ValueError):
        connect("postgresql://mamemai@localhost/mamemai", "Alice")
