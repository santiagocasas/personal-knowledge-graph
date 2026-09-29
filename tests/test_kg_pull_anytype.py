import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "knowledge_pipeline"))

from kg_pull_anytype import PullError, apply_anytype_pull, plan_anytype_pull


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class AnytypePullTests(unittest.TestCase):
    def make_graph(self, directory: Path) -> None:
        write_jsonl(
            directory / "entities.jsonl",
            [{"id": "paper:test", "name": "Test Paper", "type": "Paper"}],
        )
        write_jsonl(
            directory / "concepts.jsonl",
            [{"id": "concept:existing", "name": "Existing", "type": "Concept"}],
        )
        write_jsonl(directory / "scopes.jsonl", [])
        write_jsonl(
            directory / "relations.jsonl",
            [
                {
                    "id": "relation:preserved",
                    "subject": "paper:test",
                    "predicate": "related_to",
                    "object": "concept:existing",
                    "source_refs": [{"source_id": "canonical:test"}],
                }
            ],
        )

    def live_state(self, adopted_id: str = "") -> dict:
        return {
            "space_id": "space:test",
            "objects": [
                {
                    "anytype_id": "any:paper",
                    "type_key": "paper",
                    "name": "Test Paper",
                    "canonical_id": "paper:test",
                    "relation_values": {"about": ["any:existing", "any:new"]},
                },
                {
                    "anytype_id": "any:existing",
                    "type_key": "concept",
                    "name": "Existing",
                    "canonical_id": "concept:existing",
                    "relation_values": {},
                },
                {
                    "anytype_id": "any:new",
                    "type_key": "concept",
                    "name": "Dark Energy / Modified Gravity",
                    "canonical_id": adopted_id,
                    "relation_values": {"related_to": ["any:existing"]},
                },
            ],
        }

    def test_plan_adopts_concept_and_appends_live_relations(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_dir = Path(tmp)
            self.make_graph(graph_dir)
            plan = plan_anytype_pull(graph_dir, self.live_state())

            self.assertEqual(
                plan["concepts"][0]["id"],
                "concept:anytype:dark-energy-modified-gravity",
            )
            self.assertEqual(len(plan["stamps"]), 1)
            self.assertEqual(
                {
                    (row["subject"], row["predicate"], row["object"])
                    for row in plan["relations"]
                },
                {
                    ("paper:test", "about", "concept:existing"),
                    (
                        "paper:test",
                        "about",
                        "concept:anytype:dark-energy-modified-gravity",
                    ),
                    (
                        "concept:anytype:dark-energy-modified-gravity",
                        "related_to",
                        "concept:existing",
                    ),
                },
            )

    def test_apply_is_additive_and_repeated_pull_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_dir = Path(tmp)
            self.make_graph(graph_dir)
            plan = plan_anytype_pull(graph_dir, self.live_state())
            apply_anytype_pull(graph_dir, plan)

            relations = [
                json.loads(line)
                for line in (graph_dir / "relations.jsonl").read_text().splitlines()
            ]
            self.assertTrue(any(row["id"] == "relation:preserved" for row in relations))

            canonical_id = "concept:anytype:dark-energy-modified-gravity"
            repeated = plan_anytype_pull(graph_dir, self.live_state(canonical_id))
            self.assertEqual(repeated["concepts"], [])
            self.assertEqual(repeated["relations"], [])
            self.assertEqual(repeated["stamps"], [])

    def test_unmapped_relation_endpoint_is_reported_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_dir = Path(tmp)
            self.make_graph(graph_dir)
            state = self.live_state("concept:new")
            state["objects"] = state["objects"][:1]
            state["objects"][0]["relation_values"] = {"about": ["any:missing"]}

            plan = plan_anytype_pull(graph_dir, state)
            self.assertEqual(plan["relations"], [])
            self.assertEqual(plan["unresolved"][0]["target_anytype_id"], "any:missing")

    def test_concept_slug_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_dir = Path(tmp)
            self.make_graph(graph_dir)
            write_jsonl(
                graph_dir / "concepts.jsonl",
                [
                    {
                        "id": "concept:anytype:dark-energy-modified-gravity",
                        "name": "Different canonical concept",
                        "type": "Concept",
                    }
                ],
            )

            with self.assertRaisesRegex(PullError, "canonical ID collision"):
                plan_anytype_pull(graph_dir, self.live_state())


if __name__ == "__main__":
    unittest.main()
