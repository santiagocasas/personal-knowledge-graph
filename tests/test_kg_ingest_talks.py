import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "src" / "knowledge_pipeline"
sys.path.insert(0, str(MODULE_DIR))

from kg_ingest import _author_name, _slug, parse_scope_spec
from kg_ingest_talks import ingest_talks, parse_talks_markdown, records_from_talks


SAMPLE = """# Conference

## Table of Contents

1. Ignore this

---

## Dark Matter

### First result
**Authors:** Santiago Casas (Leiden University); Alice Example (affiliation not stated on page)

An abstract about dark matter.

### Second result
**Authors:** Alice Example (Example Institute)

Another abstract.

---

## Methods / Machine Learning

### Third result
**Authors:** Bob Example (Example Institute)

A multiline abstract.
With a second paragraph.

---

*Compiled automatically.*
"""


class TalkIngestionTests(unittest.TestCase):
    def test_parser_extracts_tracks_talks_authors_and_abstracts(self) -> None:
        talks = parse_talks_markdown(SAMPLE)

        self.assertEqual(len(talks), 3)
        self.assertEqual({talk["track"] for talk in talks}, {"Dark Matter", "Methods / Machine Learning"})
        self.assertEqual(talks[0]["title"], "First result")
        self.assertEqual(talks[0]["authors"][0], {"name": "Santiago Casas", "affiliation": "Leiden University"})
        self.assertIsNone(talks[0]["authors"][1]["affiliation"])
        self.assertEqual(talks[2]["abstract"], "A multiline abstract.\nWith a second paragraph.")
        self.assertNotIn("Compiled automatically", talks[2]["abstract"])

    def test_records_include_structural_entities_relations_and_scope(self) -> None:
        records = records_from_talks(
            parse_talks_markdown(SAMPLE),
            "source:test-talks",
            "https://example.test/conference",
            [parse_scope_spec("cosmo26=conference:Cosmo-26 2026")],
        )

        entities = {record["id"]: record for record in records["entities.jsonl"]}
        concepts = {record["id"]: record for record in records["concepts.jsonl"]}
        relations = records["relations.jsonl"]
        self.assertIn("event:conference:cosmo26-2026", entities)
        self.assertIn("talk:cosmo26:first-result", entities)
        self.assertIn("person:name:santiago-casas", entities)
        self.assertIn("institution:name:leiden-university", entities)
        self.assertNotIn("institution:name:affiliation-not-stated-on-page", entities)
        self.assertIn("concept:cosmo26-track:dark-matter", concepts)
        self.assertEqual(records["scopes.jsonl"][0]["id"], "scope:cosmo26")
        self.assertTrue(
            any(
                row["subject"] == "talk:cosmo26:first-result"
                and row["predicate"] == "presented_by"
                and row["object"] == "person:name:santiago-casas"
                for row in relations
            )
        )
        self.assertTrue(
            any(
                row["subject"] == "person:name:santiago-casas"
                and row["predicate"] == "affiliated_with"
                and row["object"] == "institution:name:leiden-university"
                for row in relations
            )
        )
        self.assertTrue(
            any(
                row["subject"] == "talk:cosmo26:first-result"
                and row["predicate"] == "about"
                and row["object"] == "concept:cosmo26-track:dark-matter"
                for row in relations
            )
        )

    def test_natural_name_matches_bibtex_normalized_name(self) -> None:
        natural_id = f"person:name:{_slug('Santiago Casas')}"
        bibtex_id = f"person:name:{_slug(_author_name('Casas, Santiago'))}"
        self.assertEqual(natural_id, bibtex_id)

    def test_ingestion_is_idempotent_and_source_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "talks.md"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            graph.mkdir()
            (graph / "entities.jsonl").write_text(
                json.dumps(
                    {
                        "id": "person:name:santiago-casas",
                        "type": "Person",
                        "rdf_type": "foaf:Person",
                        "name": "Santiago Casas",
                        "properties": {"name_as_cited": "Casas, Santiago"},
                        "source_refs": [{"source_id": "source:existing"}],
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            first = ingest_talks(
                source,
                graph,
                "source:test-talks",
                scopes=[parse_scope_spec("cosmo26=conference:Cosmo-26 2026")],
            )
            second = ingest_talks(
                source,
                graph,
                "source:test-talks",
                scopes=[parse_scope_spec("cosmo26=conference:Cosmo-26 2026")],
            )

            self.assertEqual(first["talks"], 3)
            self.assertGreater(first["changed_files"], 0)
            self.assertEqual(second["changed_files"], 0)
            for filename in ("entities.jsonl", "relations.jsonl", "aliases.jsonl", "concepts.jsonl", "scopes.jsonl"):
                rows = [json.loads(line) for line in (graph / filename).read_text(encoding="utf-8").splitlines()]
                self.assertEqual(rows, sorted(rows, key=lambda row: row["id"]))
            people = {
                row["id"]: row
                for row in (
                    json.loads(line)
                    for line in (graph / "entities.jsonl").read_text(encoding="utf-8").splitlines()
                )
            }
            shared = people["person:name:santiago-casas"]
            self.assertEqual(shared["properties"]["name_as_cited"], "Casas, Santiago")
            self.assertEqual(
                {ref["source_id"] for ref in shared["source_refs"]},
                {"source:existing", "source:test-talks"},
            )

    def test_real_source_has_expected_coverage(self) -> None:
        source = ROOT / "data" / "sources" / "cosmo26_talks.md"
        talks = parse_talks_markdown(source.read_text(encoding="utf-8"))
        self.assertEqual(len(talks), 105)
        self.assertEqual(len({talk["track"] for talk in talks}), 10)
        records = records_from_talks(
            talks,
            "indico:cosmo26:talks",
            scopes=[parse_scope_spec("cosmo26=conference:Cosmo-26 2026")],
        )
        self.assertEqual(len(records["entities.jsonl"]), 442)
        self.assertEqual(len(records["concepts.jsonl"]), 10)
        self.assertEqual(len(records["aliases.jsonl"]), 206)
        self.assertEqual(len(records["relations.jsonl"]), 1510)
        self.assertEqual(
            sum(len(talk["authors"]) for talk in talks),
            sum(relation["predicate"] == "presented_by" for relation in records["relations.jsonl"]),
        )
        object_ids = {
            row["id"]
            for filename in ("entities.jsonl", "concepts.jsonl", "scopes.jsonl")
            for row in records[filename]
        }
        self.assertFalse(
            [
                relation
                for relation in records["relations.jsonl"]
                if relation["subject"] not in object_ids or relation["object"] not in object_ids
            ]
        )


if __name__ == "__main__":
    unittest.main()
