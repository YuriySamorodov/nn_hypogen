from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import settings
from backend.main import export_all, run_pipeline
from backend.schemas import Constraints, PipelineInput, ScoringWeights
from backend.services.materials_kg import load_materials_kg_context


try:
    import streamlit as st
except Exception:
    st = None


def main() -> None:
    if st is None:
        print("Streamlit is not installed. Run `pip install -r requirements.txt` first.")
        return
    st.set_page_config(page_title="Hypothesis Factory", layout="wide")
    st.title("Hypothesis Factory for Flotation Tailings")

    hypothesis_tab, kg_tab = st.tabs(["Hypotheses", "Materials KG"])

    with kg_tab:
        st.subheader("Materials KG search")
        kg_run_id = st.text_input("KG run_id", "latest")
        kg_query = st.text_input("KG query", "316L SLM fatigue porosity biomedical applications")
        kg_top_k = st.slider("KG top-k", 3, 25, 8)
        if st.button("Search KG"):
            with st.spinner("Searching Postgres KG context..."):
                context = load_materials_kg_context(kg_run_id, kg_query, top_k=kg_top_k)
            st.caption(f"run_id={context['run_id']}")
            st.write("Evidence")
            evidence_rows = [
                {
                    "relevance": round(item.relevance, 3),
                    "source_id": item.source.source_id,
                    "source_type": item.source.source_type,
                    "section": item.source.section,
                    "text": item.text[:500],
                }
                for item in context["evidence"]
            ]
            st.dataframe(evidence_rows, use_container_width=True)
            st.write("Graph hits")
            graph_rows = [
                {
                    "subject": hit.get("subject_name") or hit.get("subject_entity_id") or "document",
                    "predicate": hit.get("predicate"),
                    "object": hit.get("object_name") or hit.get("object_value") or hit.get("object_entity_id"),
                    "confidence": hit.get("confidence"),
                    "source_file_id": hit.get("source_file_id"),
                }
                for hit in context["graph_hits"]
            ]
            st.dataframe(graph_rows, use_container_width=True)

    with st.sidebar:
        data_dir = st.text_input("Папка данных", str(settings.source_data_dir))
        target_kpi = st.text_input("KPI", "Снизить потери Ni/Cu в отвальных хвостах на 5%")
        from_db = st.checkbox("Use corpus DB", value=False)
        run_id = st.text_input("Pipeline run_id", "latest")
        no_capex = st.checkbox("Без CapEx", value=False)
        prefer_existing = st.checkbox("Предпочитать существующее оборудование", value=True)
        st.subheader("Веса")
        weights = ScoringWeights(
            kpi_impact=st.slider("KPI impact", 0.0, 0.5, 0.22, 0.01),
            feasibility=st.slider("Feasibility", 0.0, 0.5, 0.15, 0.01),
            evidence_strength=st.slider("Evidence", 0.0, 0.5, 0.15, 0.01),
            causal_consistency=st.slider("Causal", 0.0, 0.5, 0.15, 0.01),
            novelty=st.slider("Novelty", 0.0, 0.5, 0.08, 0.01),
            business_value=st.slider("Business", 0.0, 0.5, 0.10, 0.01),
            implementability=st.slider("Implementability", 0.0, 0.5, 0.10, 0.01),
            risk=st.slider("Risk penalty", 0.0, 0.5, 0.15, 0.01),
        )
        run = st.button("Generate hypotheses", type="primary")

    with hypothesis_tab:
        if not run:
            st.info("Введите KPI и запустите генерацию.")
            return

        payload = PipelineInput(
            data_dir=data_dir,
            target_kpi=target_kpi,
            constraints=Constraints(no_capex=no_capex, prefer_existing_equipment=prefer_existing),
            weights=weights,
            from_db=from_db,
            run_id=run_id,
        )
        with st.spinner("Parsing files, building RAG/KG, generating hypotheses..."):
            result = run_pipeline(payload)
            exports = export_all(result)

        st.success(f"Сгенерировано гипотез: {len(result.hypotheses)}")
        st.caption(f"JSON/CSV/PDF: {exports}")

        rows = []
        for idx, hyp in enumerate(result.hypotheses, 1):
            rows.append(
                {
                    "rank": idx,
                    "score": round(hyp.score_breakdown.final_score if hyp.score_breakdown else 0, 3),
                    "title": hyp.title,
                    "plant": hyp.target_plant,
                    "stream": hyp.target_stream,
                    "size_class": hyp.target_size_class,
                    "warnings": len(hyp.warnings),
                }
            )
        st.dataframe(rows, use_container_width=True)

        for hyp in result.hypotheses[:10]:
            with st.expander(f"{hyp.title} | score={hyp.score_breakdown.final_score:.3f}"):
                st.write(hyp.hypothesis_text)
                st.write("**Causal chain**")
                st.write(" -> ".join(hyp.causal_chain))
                st.write("**Risks**")
                st.write(hyp.risks)
                st.write("**Validation plan**")
                st.write([step.model_dump() for step in hyp.validation_plan])
                st.write("**Evidence**")
                for ev in hyp.evidence[:4]:
                    st.caption(f"{ev.source.filename} | relevance={ev.relevance:.2f}")
                    st.write(ev.text)
                if hyp.warnings:
                    st.warning("; ".join(hyp.warnings))


if __name__ == "__main__":
    main()
