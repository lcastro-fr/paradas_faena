"""Format checks for the migration files, so a file dbmate would reject fails here first."""

from __future__ import annotations

import re

import pytest
from tests.conftest import MIGRATIONS, up_section

VERSION_RE = re.compile(r"^(\d+)_")


def test_hay_migraciones():
    assert MIGRATIONS, "no se encontro ninguna migracion en migrations/"


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_formato_dbmate(path):
    text = path.read_text()

    assert "-- migrate:up" in text, "falta -- migrate:up"
    assert "-- migrate:down" in text, "falta -- migrate:down"
    assert text.index("-- migrate:up") < text.index("-- migrate:down")

    preamble = text[: text.index("-- migrate:up")]
    for line in preamble.splitlines():
        assert not line.strip() or line.lstrip().startswith("--"), (
            f"sentencia antes de -- migrate:up: {line!r}"
        )

    assert not re.search(r"^\s*(begin|commit|rollback)\s*;", text.lower(), re.M), (
        "la migracion maneja la transaccion a mano; dbmate ya la abre"
    )

    assert up_section(text).strip(), "la seccion up esta vacia"


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_version_en_el_nombre(path):
    assert VERSION_RE.match(path.name), f"{path.name} no empieza con digitos_"


def test_versiones_unicas_y_ordenadas():
    versions = [VERSION_RE.match(p.name).group(1) for p in MIGRATIONS]
    assert len(set(versions)) == len(versions), "hay versiones repetidas"
    assert versions == sorted(versions)
    assert len({len(v) for v in versions}) == 1, "el padding de versiones es inconsistente"
