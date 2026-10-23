import sqlite3
from collections.abc import Iterator

import pytest

from app.db import conectar, migrar


@pytest.fixture
def con() -> Iterator[sqlite3.Connection]:
    c = conectar(":memory:")
    migrar(c)
    yield c
    c.close()
