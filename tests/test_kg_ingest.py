import tempfile
import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "src" / "knowledge_pipeline"
import sys

sys.path.insert(0, str(MODULE_DIR))

from kg_ads import enrich_graph_from_ads, lookup_ads_title
from kg_ingest import ingest_bibtex, parse_bibtex, parse_scope_spec
from kg_materialize import load_materialization_manifest
from kg_orcid import bootstrap_orcid_works, fetch_orcid_works
from kg_inspire import fetch_inspire_works, ingest_inspire


SAMPLE = r'''Prose before the entry.
@article{Example:2026,
  author = "Doe, J. and others",
  collaboration = "Example",
  title = "{A nested {BibTeX} title}",
  eprint = "2601.00001",
  archivePrefix = "arXiv",
  primaryClass = "astro-ph.CO",
  year = "2026"
}
'''


class FakeAdsResponse:
    status_code = 200

    def json(self):
        return {
            "response": {
                "docs": [
                    {
                        "bibcode": "2026arXiv260100001D",
                        "title": ["A nested <i>BibTeX</i> title"],
                        "author": ["Doe, J.", "Roe, R.", "Poe, P."],
                        "identifier": ["arXiv:2601.00001"],
                        "year": "2026",
                        "pub": "arXiv e-prints",
                        "abstract": "An ADS abstract.",
                        "keyword": ["Cosmology", "Methods"],
                        "citation_count": 7,
                    }
                ]
            }
        }


class FakeAdsSession:
    def get(self, *args, **kwargs):
        return FakeAdsResponse()


class EuclidTitleAdsSession:
    def get(self, *args, **kwargs):
        query = kwargs.get("params", {}).get("q", "")
        if query.startswith('title:"'):
            documents = []
        else:
            documents = [{
                "bibcode": "2020A&A...642A.191E",
                "title": ["Euclid preparation. VII. Forecast validation for Euclid cosmological probes"],
                "doi": ["10.1051/0004-6361/202038071"],
                "identifier": ["arXiv:1910.09273"],
                "citation_count": 451,
            }]

        class Response:
            status_code = 200

            def json(self):
                return {"response": {"docs": documents}}

        return Response()


class FakeOrcidResponse:
    status_code = 200

    def json(self):
        summary = {
            "put-code": 42,
            "display-index": "2",
            "title": {"title": {"value": "A nested BibTeX title"}},
            "type": "journal-article",
            "publication-date": {"year": {"value": "2026"}},
            "external-ids": {
                "external-id": [
                    {
                        "external-id-type": "arxiv",
                        "external-id-value": "2601.00001",
                        "external-id-relationship": "self",
                    }
                ]
            },
        }
        duplicate = {**summary, "display-index": "1"}
        return {"group": [{"work-summary": [duplicate, summary]}]}


class FakeOrcidSession:
    def get(self, *args, **kwargs):
        return FakeOrcidResponse()


class FakeInspireSession:
    def get(self, url, **kwargs):
        class Response:
            status_code = 200

            def json(self):
                if "/authors/" in url:
                    return {"metadata": {"ids": [{"schema": "INSPIRE BAI", "value": "S.Casas.1"}]}}
                return {
                    "hits": {
                        "total": 1,
                        "hits": [{
                            "id": "123",
                            "metadata": {
                                "titles": [{"title": "A paper"}],
                                "authors": [{"full_name": "Casas, S."}],
                                "texkeys": [{"value": "Euclid:2026abc"}],
                            },
                        }],
                    }
                }

        return Response()


class BibtexIngestionTests(unittest.TestCase):
    def test_inspire_fetch_requests_texkeys(self) -> None:
        session = FakeInspireSession()
        author_bai, works = fetch_inspire_works("1075089", session)
        self.assertEqual(author_bai, "S.Casas.1")
        self.assertEqual(works[0]["metadata"]["texkeys"][0]["value"], "Euclid:2026abc")

    def test_inspire_ingestion_stores_citation_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            graph = Path(temporary) / "graph"
            report = ingest_inspire(graph, "1075089", dry_run=False, session=FakeInspireSession())
            self.assertEqual(report["papers"], 1)
            entities = (graph / "entities.jsonl").read_text(encoding="utf-8")
            self.assertIn('"citation_key": "Euclid:2026abc"', entities)

    def test_ads_title_fallback_ignores_punctuation_variants(self) -> None:
        document = lookup_ads_title(
            "Euclid preparation: VII. Forecast validation for Euclid cosmological probes",
            "token",
            EuclidTitleAdsSession(),
        )
        self.assertEqual(document["bibcode"], "2020A&A...642A.191E")
        self.assertEqual(document["citation_count"], 451)

    def test_parser_preserves_nested_braces(self) -> None:
        entries = parse_bibtex(SAMPLE)
        self.assertEqual(entries[0]["citation_key"], "Example:2026")
        self.assertEqual(entries[0]["fields"]["title"], "A nested {BibTeX} title")

    def test_ingestion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")

            first = ingest_bibtex(source, graph, "source:test")
            second = ingest_bibtex(source, graph, "source:test")

            self.assertEqual(first["entries"], 1)
            self.assertEqual(first["changed_files"], 4)
            self.assertEqual(second["changed_files"], 0)
            entities = (graph / "entities.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "Paper"', entities)
            self.assertIn('"name": "J. Doe"', entities)

            manifest = load_materialization_manifest(graph, "space:test")
            self.assertEqual(len(manifest["objects"]), 4)
            self.assertEqual(len(manifest["relations"]), 3)
            self.assertEqual(manifest["space_id"], "space:test")

    def test_scoped_ingestion_builds_browsable_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            scope = parse_scope_spec("my-papers=portfolio:My Papers")

            first = ingest_bibtex(source, graph, "source:test", scopes=[scope])
            second = ingest_bibtex(source, graph, "source:test", scopes=[scope])

            self.assertEqual(first["changed_files"], 5)
            self.assertEqual(first["scopes"], ["my-papers"])
            self.assertEqual(second["changed_files"], 0)
            self.assertIn('"name": "My Papers"', (graph / "scopes.jsonl").read_text(encoding="utf-8"))

            manifest = load_materialization_manifest(graph, "space:test")
            self.assertEqual(len(manifest["objects"]), 6)
            self.assertEqual(len(manifest["relations"]), 12)
            scope_object = next(item for item in manifest["objects"] if item["type_key"] == "graph_scope")
            self.assertEqual(scope_object["relation_properties"], ["members", "list_view"])
            collection = next(item for item in manifest["objects"] if item["type_key"] == "collection")
            self.assertEqual(collection["name"], "My Papers list")
            self.assertEqual(manifest["collections"][0]["canonical_id"], collection["canonical_id"])
            self.assertEqual(len(manifest["collections"][0]["members"]), 4)

    def test_ads_enrichment_is_idempotent_and_projection_caps_authors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            ingest_bibtex(source, graph, "source:test")

            first = enrich_graph_from_ads(graph, "token", session=FakeAdsSession())
            second = enrich_graph_from_ads(graph, "token", session=FakeAdsSession())

            self.assertEqual(first["matched"], 1)
            self.assertEqual(first["changed_files"], 2)
            self.assertEqual(second["changed_files"], 0)
            entities = (graph / "entities.jsonl").read_text(encoding="utf-8")
            self.assertIn('"ads_bibcode": "2026arXiv260100001D"', entities)
            self.assertIn('"author_count": 3', entities)
            self.assertIn('"description": "An ADS abstract."', entities)
            self.assertIn('"name": "A nested BibTeX title"', entities)

            manifest = load_materialization_manifest(
                graph,
                "space:test",
                {"full_expansion_max_authors": 2, "large_author_list_first_authors": 1},
            )
            paper = next(item for item in manifest["objects"] if item["type_key"] == "paper")
            self.assertEqual(paper["properties"]["author_list"], "Doe, J.; and 2 additional authors")

    def test_orcid_bootstrap_collapses_groups_and_deduplicates_existing_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            ingest_bibtex(source, graph, "source:test")

            works = fetch_orcid_works("0000-0000-0000-000X", FakeOrcidSession())
            self.assertEqual(len(works), 1)
            self.assertEqual(works[0]["display-index"], "2")

            report = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                dry_run=True,
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )
            self.assertEqual(report["orcid_work_groups"], 1)
            self.assertEqual(report["existing_papers"], 1)
            self.assertEqual(report["new_papers"], 0)
            self.assertEqual(report["ads_matched"], 1)
            self.assertEqual(report["researcher"], "person:name:s-casas")
            self.assertEqual(report["coauthor_links"], 3)
            self.assertEqual(report["scope"], "my-papers")
            self.assertFalse((graph / "scopes.jsonl").exists())

            applied = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )
            self.assertEqual(applied["changed_files"], 4)
            second = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )
            self.assertEqual(second["changed_files"], 0)

            manifest = load_materialization_manifest(graph, "space:test")
            paper = next(item for item in manifest["objects"] if item["type_key"] == "paper")
            people = [item for item in manifest["objects"] if item["type_key"] == "person"]
            authors = {
                relation["object"]
                for relation in manifest["relations"]
                if relation["subject"] == paper["canonical_id"]
                and relation["property_key"] == "authored_by"
            }
            self.assertEqual(len(people), 4)
            self.assertEqual(len(authors), 4)
            researcher = next(item for item in people if item["canonical_id"] == "person:name:s-casas")
            self.assertEqual(researcher["name"], "Santiago Casas")
            self.assertEqual(researcher["properties"]["orcid"], "https://orcid.org/0000-0000-0000-000X")

    def test_orcid_bootstrap_reconciles_ads_paper_outside_orcid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            ingest_bibtex(source, graph, "source:test")

            entities_path = graph / "entities.jsonl"
            entities = [json.loads(line) for line in entities_path.read_text(encoding="utf-8").splitlines()]
            entities.append(
                {
                    "id": "paper:ads:outside-orcid",
                    "type": "Paper",
                    "rdf_type": "bibo:AcademicArticle",
                    "name": "Outside ORCID",
                    "identifiers": {"ads": "2026Test....1C"},
                    "properties": {
                        "author_list": "Casas, S.; Alpha, A.; Beta, B.; Gamma, G.",
                        "author_count": 4,
                    },
                    "source_refs": [{"source_id": "source:reading-list"}],
                }
            )
            entities_path.write_text(
                "".join(json.dumps(record, sort_keys=True) + "\n" for record in entities),
                encoding="utf-8",
            )

            first = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )
            second = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )

            self.assertEqual(first["derived_author_papers"], 1)
            self.assertEqual(second["changed_files"], 0)
            relations = [
                json.loads(line)
                for line in (graph / "relations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            outside_authors = {
                relation["object"]
                for relation in relations
                if relation["subject"] == "paper:ads:outside-orcid"
                and relation["predicate"] == "authored_by"
            }
            self.assertEqual(
                outside_authors,
                {
                    "person:name:s-casas",
                    "person:name:a-alpha",
                    "person:name:b-beta",
                    "person:name:g-gamma",
                },
            )

    def test_orcid_bootstrap_reconciles_euclid_corporate_authorship(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bib"
            graph = root / "graph"
            source.write_text(SAMPLE, encoding="utf-8")
            ingest_bibtex(source, graph, "source:test")

            entities_path = graph / "entities.jsonl"
            entities = [json.loads(line) for line in entities_path.read_text(encoding="utf-8").splitlines()]
            entities.extend(
                [
                    {
                        "id": "paper:ads:euclid-series",
                        "type": "Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "name": "Euclid: A consortium result",
                        "identifiers": {"ads": "2026Test....2E"},
                        "properties": {"author_list": "Alpha, A.; Beta, B."},
                        "source_refs": [{"source_id": "source:reading-list"}],
                    },
                    {
                        "id": "paper:ads:euclid-marker",
                        "type": "Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "name": "A survey result",
                        "identifiers": {"ads": "2026Test....3E"},
                        "properties": {"author_list": "Alpha, A.; Euclid Collaboration"},
                        "source_refs": [{"source_id": "source:reading-list"}],
                    },
                    {
                        "id": "paper:ads:euclid-hyphen",
                        "type": "Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "name": "Euclid preparation - LXXXIV. A consortium result",
                        "identifiers": {"ads": "2026Test....4E"},
                        "properties": {"author_list": "Alpha, A.; Beta, B."},
                        "source_refs": [{"source_id": "source:reading-list"}],
                    },
                    {
                        "id": "paper:ads:euclid-period",
                        "type": "Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "name": "Euclid. A space mission result",
                        "identifiers": {"ads": "2026Test....5E"},
                        "properties": {"author_list": "Alpha, A.; Beta, B."},
                        "source_refs": [{"source_id": "source:reading-list"}],
                    },
                    {
                        "id": "paper:ads:not-euclid",
                        "type": "Paper",
                        "rdf_type": "bibo:AcademicArticle",
                        "name": "Euclidean geometry in cosmology",
                        "identifiers": {"ads": "2026Test....6N"},
                        "properties": {"author_list": "Alpha, A.; Beta, B."},
                        "source_refs": [{"source_id": "source:reading-list"}],
                    },
                ]
            )
            entities_path.write_text(
                "".join(json.dumps(record, sort_keys=True) + "\n" for record in entities),
                encoding="utf-8",
            )

            first = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )
            second = bootstrap_orcid_works(
                graph,
                "0000-0000-0000-000X",
                "token",
                orcid_session=FakeOrcidSession(),
                ads_session=FakeAdsSession(),
            )

            self.assertEqual(first["corporate_author_papers"], 4)
            self.assertEqual(second["changed_files"], 0)
            relations = [
                json.loads(line)
                for line in (graph / "relations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            corporate_subjects = {
                relation["subject"]
                for relation in relations
                if relation["predicate"] == "corporate_authored_by"
                and relation["object"] == "institution:name:euclid-collaboration"
            }
            self.assertEqual(
                corporate_subjects,
                {
                    "paper:ads:euclid-series",
                    "paper:ads:euclid-marker",
                    "paper:ads:euclid-hyphen",
                    "paper:ads:euclid-period",
                },
            )


if __name__ == "__main__":
    unittest.main()
