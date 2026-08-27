import json
import sys
import tempfile
import unittest
from pathlib import Path

from rdflib import Dataset


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "knowledge_pipeline"))

from kg_rdf import export_trig, query_graph


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


class RdfGraphTests(unittest.TestCase):
    def test_query_and_trig_export_preserve_relations_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph_dir = Path(temporary) / "graph"
            graph_dir.mkdir()
            source_ref = [{"source_id": "test:catalog", "source_url": "https://example.test"}]
            write_jsonl(
                graph_dir / "entities.jsonl",
                [
                    {
                        "id": "paper:doi:10.1234/example",
                        "name": "Example Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "properties": {"citation_count": 12},
                        "source_refs": source_ref,
                    },
                    {
                        "id": "institution:name:example",
                        "name": "Example Collaboration",
                        "rdf_type": "foaf:Organization",
                        "source_refs": source_ref,
                    },
                ],
            )
            write_jsonl(
                graph_dir / "relations.jsonl",
                [
                    {
                        "id": "relation:example",
                        "subject": "paper:doi:10.1234/example",
                        "predicate": "corporate_authored_by",
                        "object": "institution:name:example",
                        "source_refs": source_ref,
                    }
                ],
            )
            write_jsonl(
                graph_dir / "aliases.jsonl",
                [
                    {
                        "id": "alias:example",
                        "entity_id": "paper:doi:10.1234/example",
                        "alias": "Example et al. (2026)",
                        "source_refs": source_ref,
                    }
                ],
            )

            rows = query_graph(
                graph_dir,
                ROOT / "ontologies" / "cosmology.yaml",
                """
                PREFIX pkg: <https://w3id.org/personal-kg/ontology/>
                PREFIX schema: <https://schema.org/>
                SELECT ?name ?citations WHERE {
                  ?paper pkg:corporate_authored_by ?institution ;
                         schema:name ?name ;
                         pkg:citation_count ?citations .
                  ?institution schema:name "Example Collaboration" .
                }
                """,
            )
            self.assertEqual(rows, [{"name": "Example Paper", "citations": 12}])

            output = Path(temporary) / "kg.trig"
            report = export_trig(
                graph_dir,
                ROOT / "ontologies" / "cosmology.yaml",
                output,
            )
            self.assertEqual(report["named_graphs"], 1)
            self.assertGreaterEqual(report["triples"], 8)

            exported = Dataset(default_union=True)
            exported.parse(output, format="trig")
            named_graphs = [
                graph
                for graph in exported.graphs()
                if graph.identifier != exported.default_graph.identifier
            ]
            self.assertEqual(len(named_graphs), 1)
            self.assertEqual(len(exported), report["triples"])


if __name__ == "__main__":
    unittest.main()
