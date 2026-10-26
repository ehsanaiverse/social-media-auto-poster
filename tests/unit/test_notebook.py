"""Checks on notebooks/workflow.ipynb that need no Jupyter install."""

import ast
import json
from pathlib import Path

NOTEBOOK = Path(__file__).resolve().parents[2] / "notebooks" / "workflow.ipynb"


def code_cells() -> list[dict]:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_notebook_is_committed_with_outputs_cleared():
    for cell in code_cells():
        assert cell["outputs"] == [], "Clear all outputs before committing the notebook."
        assert cell["execution_count"] is None, "Clear execution counts before committing the notebook."


def test_every_code_cell_is_valid_python():
    for cell in code_cells():
        ast.parse("".join(cell["source"]))


def test_notebook_checks_tracing_before_anything_else():
    first = "".join(code_cells()[0]["source"])
    assert "LANGSMITH_TRACING" in first and "LANGCHAIN_TRACING_V2" in first
