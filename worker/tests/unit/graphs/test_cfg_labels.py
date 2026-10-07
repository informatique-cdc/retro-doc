"""Unit tests for the labels of the Java CFG nodes."""

from typing import Any

from app.graphs.service.cfg_builder import JavaCFGBuilderService
from app.graphs.service.preparer import JavaSourcePreparerService


def _labels(body: str) -> list[str]:
    source = f"class Compte {{\n  void traiter(java.util.List<String> lignes, int n) {{\n{body}\n  }}\n}}\n"
    tree = JavaSourcePreparerService().prepare(source, "Compte.java")
    (cfg,) = JavaCFGBuilderService().build(source, "Compte.java", tree)
    nodes: list[dict[str, Any]] = cfg["nodes"]
    return [n["label"] for n in nodes if n["type"] not in ("entry", "exit")]


def test_cfg_label_keeps_the_qualifier_of_a_call() -> None:
    # javalang stores a call/field qualifier as a plain `str`
    labels = _labels(
        'lignes.add("x");\n'
        'System.out.println("Solde : " + n);\n'
        "if (lignes.isEmpty()) { n = Integer.MAX_VALUE; }"
    )

    assert labels == [
        'lignes.add("x")',
        'System.out.println("Solde : " + n)',
        "if (lignes.isEmpty())",
        "n = Integer.MAX_VALUE",
    ]


def test_cfg_label_renders_literals_as_written() -> None:
    labels = _labels("n = 42;\nwhile (n > 0) { n = n - 1; }\nchar c = 'c';")

    assert labels == ["n = 42", "while (n > 0)", "n = n - 1", "char c = 'c'"]
