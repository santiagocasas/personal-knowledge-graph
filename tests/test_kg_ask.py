import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
PROFILE = ROOT / "ontologies" / "cosmology.yaml"
sys.path.insert(0, str(ROOT / "src" / "knowledge_pipeline"))

from kg_ask import AskError, build_schema_card, clean_query, ask, validate_query


class KgAskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.graph = Path(self.temp.name) / "graph"
        self.graph.mkdir()
        self.queries = Path(self.temp.name) / "queries"
        self._write("entities.jsonl", [
            {"id": "paper:example", "name": "Example Paper", "rdf_type": "bibo:AcademicArticle", "properties": {"citation_count": 12}, "source_refs": []},
            {"id": "person:name:doe", "name": "Jane Doe", "rdf_type": "foaf:Person", "properties": {}, "source_refs": []},
            {"id": "institution:example", "name": "Example Institute", "rdf_type": "foaf:Organization", "properties": {}, "source_refs": []},
        ])
        self._write("relations.jsonl", [{"id": "rel:1", "subject": "paper:example", "predicate": "authored_by", "object": "person:name:doe", "source_refs": []}])
        self._write("aliases.jsonl", [{"alias": "Doe, J.", "entity_id": "person:name:doe", "kind": "cited", "source_refs": []}])

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, filename, rows):
        (self.graph / filename).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_schema_uses_canonical_predicate_names(self):
        card = build_schema_card(self.graph)
        self.assertIn("authored_by", card["known_pkg_terms"])
        self.assertNotIn("corporate_authors", card["known_pkg_terms"])

    def test_invalid_predicate_is_rejected(self):
        card = build_schema_card(self.graph)
        query = clean_query("SELECT ?name WHERE { ?paper pkg:corporate_authors ?x . ?paper schema:name ?name . }")
        problems = validate_query(query, card)
        self.assertTrue(any("corporate_authors" in problem for problem in problems))

    def test_clean_query_removes_fences_and_prefixes(self):
        query = clean_query("```sparql\nPREFIX pkg: <wrong>\nSELECT * WHERE { ?x ?p ?o }\n```")
        self.assertEqual(query.count("PREFIX pkg:"), 1)
        self.assertNotIn("wrong", query)

    def test_valid_query_executes_and_saves(self):
        generated = "SELECT ?name WHERE { ?paper a bibo:AcademicArticle ; schema:name ?name . }"
        report = ask("Which paper exists?", self.graph, PROFILE, self.queries, execute=True, llm=lambda _: generated)
        self.assertEqual(report["row_count"], 1)
        self.assertTrue(Path(report["query_file"]).exists())

    def test_invalid_query_is_repaired(self):
        responses = iter([
            "SELECT ?name WHERE { ?paper pkg:corporate_authors ?x ; schema:name ?name . }",
            "SELECT ?name WHERE { ?paper a bibo:AcademicArticle ; schema:name ?name . }",
        ])
        prompts = []
        def fake(messages):
            prompts.append(messages)
            return next(responses)
        report = ask("Which paper exists?", self.graph, PROFILE, self.queries, llm=fake)
        self.assertEqual(report["attempts"], 2)
        self.assertTrue(any("corporate_authors" in message["content"] for message in prompts[1]))

    def test_exhausted_repairs_raise_without_writing(self):
        with self.assertRaises(AskError):
            ask("Bad query", self.graph, PROFILE, self.queries, max_repairs=1, llm=lambda _: "SELECT * WHERE { ?x pkg:not_real ?y . }")
        self.assertFalse((self.queries / "generated").exists())

    def test_unanswerable_raises(self):
        with self.assertRaises(AskError):
            ask("What is the weather?", self.graph, PROFILE, self.queries, llm=lambda _: "UNANSWERABLE")


if __name__ == "__main__":
    unittest.main()
