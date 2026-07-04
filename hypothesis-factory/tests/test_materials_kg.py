from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.kg_worker import main as kg_worker_main
from backend.schemas import SourceDocument
from backend.services.chunking import chunk_document
from backend.services.corpus_db import CorpusStore
from backend.services.materials_kg import build_document_layers, build_embeddings, build_entities, build_relations, load_materials_kg_context
from backend.services.scientific_pdf import parse_grobid_tei


TEI_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>316L steel by SLM</title></titleStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <author><persName><forename>Ada</forename><surname>Lovelace</surname></persName></author>
          </analytic>
          <idno type="DOI">10.1234/kg.test</idno>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc><abstract><p>316L stainless steel processed by SLM has fatigue sensitivity to porosity.</p></abstract></profileDesc>
  </teiHeader>
  <text>
    <body>
      <div><head>Methods</head><p>EBSD and SEM were used on 316L samples.</p></div>
      <figure type="table"><head>Table 1</head><figDesc>Mechanical properties</figDesc></figure>
    </body>
    <back><listBibl><biblStruct><analytic><title>Prior fatigue study</title></analytic></biblStruct></listBibl></back>
  </text>
</TEI>
"""


class MaterialsKGTests(unittest.TestCase):
    def test_grobid_tei_parser_extracts_sections_assets_metadata(self):
        sections, assets, metadata = parse_grobid_tei(TEI_FIXTURE, "src1")

        self.assertEqual(metadata["doi"], "10.1234/kg.test")
        self.assertIn("Ada Lovelace", metadata["authors"])
        self.assertTrue(any(section["section_type"] == "abstract" for section in sections))
        self.assertTrue(any(section["title"] == "Methods" for section in sections))
        self.assertEqual(assets[0]["asset_type"], "table")

    def test_kg_build_extracts_material_entities_relations_and_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            root = Path(tmp) / "corpus"
            root.mkdir()
            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "kg", {})
            doc = SourceDocument(
                id="src1",
                path=str(root / "note.txt"),
                source_type="txt",
                title="316L SLM note",
                text="316L stainless steel processed by SLM shows fatigue issues caused by porosity. EBSD confirms austenite.",
                metadata={"relative_path": "note.txt"},
            )
            source_id = store.upsert_external_source(run_id, "note", "openalex", doc.title, {"id": "note"})
            doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "test"})
            store.replace_chunks(run_id, source_id, chunk_document(doc, size=500, overlap=50))

            layer_counts = build_document_layers(store, run_id, grobid="off")
            entities = build_entities(store, run_id)
            relations = build_relations(store, run_id, entities)
            embeddings = build_embeddings(store, run_id)
            status = store.run_status(run_id)
            store.close()

            self.assertGreaterEqual(layer_counts["sections"], 1)
            self.assertTrue(any(entity["normalized"] == "316l" for entity in entities))
            self.assertTrue(any(entity["entity_type"] == "property" and entity["normalized"] == "fatigue" for entity in entities))
            self.assertTrue(any(relation["predicate"] == "has_property" for relation in relations))
            self.assertGreaterEqual(len(embeddings), 3)
            self.assertGreaterEqual(status["kg_entities"], 4)
            self.assertGreaterEqual(status["kg_relations"], 1)
            self.assertGreaterEqual(status["kg_embeddings"], 3)

    def test_kg_worker_build_and_search_cli_without_external_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            root = Path(tmp) / "corpus"
            root.mkdir()
            store = CorpusStore(db_url)
            store.initialize_schema()
            run_id = store.create_run(root, "kg-cli", {})
            doc = SourceDocument(
                id="src1",
                path=str(root / "paper.txt"),
                source_type="txt",
                title="Ti-6Al-4V paper",
                text="Ti-6Al-4V titanium alloy was studied by EBSD for biomedical implant applications.",
                metadata={"relative_path": "paper.txt"},
            )
            source_id = store.upsert_external_source(run_id, "paper", "openalex", doc.title, {"id": "paper"})
            doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "test"})
            store.replace_chunks(run_id, source_id, chunk_document(doc, size=500, overlap=50))
            store.close()

            with redirect_stdout(StringIO()):
                exit_code = kg_worker_main(
                    [
                        "--database-url",
                        db_url,
                        "build",
                        "--run-id",
                        run_id,
                        "--stages",
                        "sections,entities,relations,embeddings,sync",
                        "--grobid",
                        "off",
                        "--neo4j",
                        "off",
                        "--qdrant",
                        "off",
                    ]
                )
            context = load_materials_kg_context(run_id, "Ti-6Al-4V biomedical EBSD", db_url, top_k=5)

            self.assertEqual(exit_code, 0)
            self.assertGreaterEqual(len(context["evidence"]), 1)
            self.assertGreaterEqual(len(context["graph_hits"]), 1)

    def test_kg_worker_build_all_processes_multiple_runs_with_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_url = f"sqlite:///{Path(tmp) / 'corpus.db'}"
            root = Path(tmp) / "corpus"
            root.mkdir()
            store = CorpusStore(db_url)
            store.initialize_schema()
            run_ids = []
            for idx, text in enumerate(
                [
                    "316L stainless steel processed by SLM has fatigue sensitivity to porosity.",
                    "Ti-6Al-4V titanium alloy was studied by EBSD for biomedical implants.",
                ],
                1,
            ):
                run_id = store.create_run(root, f"kg-cli-{idx}", {})
                run_ids.append(run_id)
                doc = SourceDocument(
                    id=f"src{idx}",
                    path=str(root / f"paper{idx}.txt"),
                    source_type="txt",
                    title=f"paper {idx}",
                    text=text,
                    metadata={"relative_path": f"paper{idx}.txt"},
                )
                source_id = store.upsert_external_source(run_id, f"paper{idx}", "openalex", doc.title, {"id": f"paper{idx}"})
                doc = store.save_document(run_id, source_id, doc, {"text_chars": len(doc.text), "ocr_required": False, "extractor": "test"})
                store.replace_chunks(run_id, source_id, chunk_document(doc, size=500, overlap=50))
            store.close()

            with redirect_stdout(StringIO()):
                exit_code = kg_worker_main(
                    [
                        "--database-url",
                        db_url,
                        "build-all",
                        "--stages",
                        "sections,entities,relations,embeddings,sync",
                        "--grobid",
                        "off",
                        "--neo4j",
                        "off",
                        "--qdrant",
                        "off",
                    ]
                )

            store = CorpusStore(db_url)
            try:
                self.assertEqual(exit_code, 0)
                for run_id in run_ids:
                    status = store.run_status(run_id)
                    self.assertGreater(status["kg_embeddings"], 0)
                    self.assertTrue(any(row["target"] == "kg_worker" and row["status"] == "completed" for row in status["kg_sync_status"]))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
