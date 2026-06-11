"""Table detection: number-dense prose must NOT be classified as a table;
real reconstructed grids must be. Plus the re-classification pass."""
from atf_graphrag.ingestion.chunker import _detect_type, _is_table_row
from atf_graphrag.indexing.reclassify import _is_real_table


PROSE = ("From January 1, 2015, through December 31, 2015, BATS captured a total "
         "of 21,502 fire-related incidents across 50 states and 3 territories.")

GRID = ("[EXTRACTED TABLE]\n| Areas of Origin | Total |\n| --- | --- |\n"
        "| Storage Areas | 323 |\n| Structural Areas | 327 |\n| Other | 500 |")


def test_numeric_prose_is_not_a_table_row():
    assert _is_table_row(PROSE) is False              # was True (the bug)


def test_markdown_row_is_a_table_row():
    assert _is_table_row("| Storage Areas | 323 |") is True


def test_columnar_row_is_a_table_row():
    assert _is_table_row("Storage Areas    323    327") is True


def test_detect_type_prose_is_text():
    assert _detect_type(PROSE.splitlines()) == "text"


def test_detect_type_grid_is_table():
    assert _detect_type(GRID.splitlines()) == "table"


def test_is_real_table_discriminates():
    assert _is_real_table(GRID) is True
    assert _is_real_table("[TABLE: STRATEGIC HIGHLIGHTS]\n" + PROSE) is False


def test_reclassify_demotes_prose_keeps_grid():
    from atf_graphrag.indexing.reclassify import reclassify_corpus

    class _VS:
        def __init__(self):
            self._payloads = {
                "a": {"content_type": "table", "extraction_method": "table_extraction",
                      "text": "[TABLE: HIGHLIGHTS]\n" + PROSE},
                "b": {"content_type": "table", "extraction_method": "table_extraction",
                      "text": GRID},
                "c": {"content_type": "text", "text": "ordinary prose"},
            }
            self.committed = False

        def commit(self):
            self.committed = True

    vs = _VS()
    r = reclassify_corpus(vs)
    assert r["demoted_table"] == 1 and r["kept_table"] == 1
    assert vs._payloads["a"]["content_type"] == "text"      # prose demoted
    assert vs._payloads["a"]["extraction_method"] == "text"
    assert vs._payloads["b"]["content_type"] == "table"     # grid kept
    assert vs.committed is True
