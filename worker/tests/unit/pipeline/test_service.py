"""Unit tests for the pipeline service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.language import Language
from app.graphs.service import get_graph_services
from app.pipeline.service import _build_graphs, get_source_code_from_blob


def _container_returning(raw: bytes) -> MagicMock:
    """A container client whose blob download yields `raw`."""
    downloader = MagicMock()
    downloader.readall = AsyncMock(return_value=raw)
    container = MagicMock()
    container.download_blob = AsyncMock(return_value=downloader)
    return container


def _download(raw: bytes) -> str:
    with patch(
        "app.pipeline.service.get_container_client",
        return_value=_container_returning(raw),
    ):
        return asyncio.run(get_source_code_from_blob("user/u/r/src/p/Compte.java"))


def test_get_source_code_from_blob_strips_utf8_bom() -> None:
    # Windows editors (Notepad, Visual Studio, Eclipse "UTF-8 BOM"...) prefix
    # UTF-8 files with a byte order mark
    source = "package fr.cdc;\n\npublic class Compte {\n  void crediter() {}\n}\n"

    decoded = _download(b"\xef\xbb\xbf" + source.encode("utf-8"))

    assert decoded == source
    ast, cfgs, dfgs = _build_graphs(
        get_graph_services(Language.JAVA), decoded, "Compte.java", "Compte.java"
    )
    assert ast is not None
    assert cfgs is not None
    assert dfgs is not None


def test_get_source_code_from_blob_decodes_windows_1252() -> None:
    # Legacy Java code bases are often encoded in ISO-8859-1 / Windows-1252
    source = (
        "package fr.cdc;\n"
        "// Règle de gestion : calcul de la rémunération\n"
        'class Paie { String libelle = "Prime d\'été"; }\n'
    )

    assert _download(source.encode("cp1252")) == source


def test_get_source_code_from_blob_keeps_utf8() -> None:
    source = 'class Paie { String libelle = "Prime d\'été"; }\n'

    assert _download(source.encode("utf-8")) == source
