import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "knowledge_pipeline"))

from kg_export_bibtex import author_position, export_bibtex, render_bibtex


def paper(identifier: str, name: str, authors: str, **properties):
    return {
        "id": identifier,
        "type": "Paper",
        "rdf_type": "bibo:AcademicArticle",
        "name": name,
        "properties": {"author_list": authors, "citation_key": identifier, **properties},
        "identifiers": {},
    }


class BibtexExportTests(unittest.TestCase):
    def test_author_position_variants(self):
        self.assertEqual(author_position("Alpha, A.; Santiago Casas"), 2)
        self.assertEqual(author_position("Alpha, A.; Casas, S."), 2)
        self.assertIsNone(author_position("Alpha, A.; Santos, S."))

    def test_categories_and_keywords(self):
        papers = [
            paper("personal", "Personal", "Casas, S.; Alpha, A."),
            paper("core", "Core", "; ".join(["Casas, S."] + [f"Author {n}, A." for n in range(10)])),
            paper("collab", "Collab", "; ".join([f"Author {n}, A." for n in range(10)] + ["Casas, S."])),
            paper("unresolved", "Unresolved", "Euclid Collaboration; Author, A."),
        ]
        relations = [
            {"subject": "core", "predicate": "corporate_authored_by", "object": "institution:name:euclid-collaboration"},
            {"subject": "collab", "predicate": "corporate_authored_by", "object": "institution:name:euclid-collaboration"},
            {"subject": "unresolved", "predicate": "corporate_authored_by", "object": "institution:name:euclid-collaboration"},
        ]
        rendered, report = render_bibtex(papers, relations)
        self.assertEqual(report["categories"], {"personal": 1, "euclid_core": 1, "euclid_collab": 2})
        self.assertIn('keywords = "euclid_core"', rendered)
        self.assertIn('collaboration = "Euclid"', rendered)
        self.assertIn('author = "Casas, S. and Author 0, A. and Author 1, A.', rendered)

    def test_metadata_collaboration_fallback_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph = root / "graph"
            graph.mkdir()
            graph.joinpath("entities.jsonl").write_text(
                '{"id":"p","type":"Paper","name":"Title","properties":{"citation_key":"Key","author_list":"Casas, S.","collaboration":"Euclid"},"identifiers":{}}\n',
                encoding="utf-8",
            )
            graph.joinpath("relations.jsonl").write_text("", encoding="utf-8")
            output = root / "exports" / "publications.bib"
            first = export_bibtex(graph, output)
            second = export_bibtex(graph, output)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertIn('keywords = "euclid_core"', output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
