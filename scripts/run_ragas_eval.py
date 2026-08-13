"""Runs RAGAS metrics (section 24.1: faithfulness, answer relevancy, context
precision, context recall) over the chat evaluation log Phase 9's
agents/ragas_logging.py wrote (section 24.2's record format).

MUST be run with .venv-ragas's interpreter, not the main app's .venv --
current ragas releases (0.1.x through 0.4.x) all import a langchain_community
submodule (chat_models.vertexai) that was removed from langchain-community's
0.4.x line, which the main app's langgraph/langchain-core stack requires.
.venv-ragas pins an older, mutually-compatible langchain-core/langchain-community/
ragas/langchain-google-genai combination instead, fully isolated from the main
app's dependencies (see requirements-ragas.txt). This script is invoked as a
subprocess from scripts/run_evaluation.py (main venv) precisely so the two
dependency trees never need to coexist in one process.

Usage:
    .venv-ragas/Scripts/python.exe scripts/run_ragas_eval.py [--input PATH] [--limit N]

Prints one JSON object to stdout: {"cases": [...], "aggregate": {...}}.
Any case the log has no answer for (evidence_rejected/escalated -- no
generated text to score) is skipped, not scored as 0, since RAGAS's metrics
are undefined for "no answer was produced."
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "eval_cache", "chat_ragas_log.jsonl",
)


def _load_cases(path: str, limit: int | None) -> list[dict]:
    cases = []
    if not os.path.exists(path):
        return cases
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not record.get("answer") or not record.get("contexts"):
                continue  # nothing to score faithfulness/relevancy/precision against
            cases.append(record)
    if limit:
        cases = cases[-limit:]
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_LOG_PATH)
    parser.add_argument("--limit", type=int, default=None, help="score only the most recent N cases")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print(json.dumps({"error": "GEMINI_API_KEY not set; cannot run RAGAS (needs a judge LLM)."}))
        sys.exit(1)

    cases = _load_cases(args.input, args.limit)
    if not cases:
        print(json.dumps({"cases": [], "aggregate": {}, "note": "no scorable cases found in the log"}))
        return

    from datasets import Dataset
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    class _CompatChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
        """ragas's LangchainLLMWrapper always calls generate_prompt(temperature=...),
        which BaseChatModel forwards as a **kwargs entry into _generate/_agenerate.
        This langchain-google-genai/google-generativeai version pairing (see
        requirements-ragas.txt) forwards that straight to the raw SDK's
        generate_content(), which doesn't accept temperature as a top-level kwarg
        there (it belongs inside generation_config) -- raises "unexpected keyword
        argument 'temperature'". Folding it into generation_config here instead
        fixes the call without needing a different, less-tested version pairing."""

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            temperature = kwargs.pop("temperature", None)
            if temperature is not None:
                generation_config = dict(kwargs.pop("generation_config", None) or {})
                generation_config.setdefault("temperature", temperature)
                kwargs["generation_config"] = generation_config
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            temperature = kwargs.pop("temperature", None)
            if temperature is not None:
                generation_config = dict(kwargs.pop("generation_config", None) or {})
                generation_config.setdefault("temperature", temperature)
                kwargs["generation_config"] = generation_config
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    judge_llm = _CompatChatGoogleGenerativeAI(model="gemini-flash-lite-latest", google_api_key=api_key)
    judge_embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001", google_api_key=api_key)

    dataset_rows = {
        "question": [c["question"] for c in cases],
        "answer": [c["answer"] for c in cases],
        "contexts": [c["contexts"] for c in cases],
        # context_recall needs a reference; fall back to the answer itself when no
        # human reference_answer exists, consistent with treating the approved
        # answer as ground truth absent a separately labeled reference (documented
        # in the result as reference_source so this substitution is never silently invisible)
        "ground_truth": [c.get("reference_answer") or c["answer"] for c in cases],
    }
    dataset = Dataset.from_dict(dataset_rows)

    result = evaluate(
        dataset, metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=judge_llm, embeddings=judge_embeddings,
    )
    per_case_df = result.to_pandas()

    per_case = []
    for i, c in enumerate(cases):
        row = per_case_df.iloc[i]
        per_case.append({
            "question": c["question"],
            "document_id": c.get("document_id"),
            "faithfulness": float(row["faithfulness"]) if row["faithfulness"] == row["faithfulness"] else None,
            "answer_relevancy": float(row["answer_relevancy"]) if row["answer_relevancy"] == row["answer_relevancy"] else None,
            "context_precision": float(row["context_precision"]) if row["context_precision"] == row["context_precision"] else None,
            "context_recall": float(row["context_recall"]) if row["context_recall"] == row["context_recall"] else None,
            "reference_source": "labeled" if c.get("reference_answer") else "answer_as_reference",
        })

    def _mean(key: str) -> float | None:
        values = [c[key] for c in per_case if c[key] is not None]
        return round(sum(values) / len(values), 4) if values else None

    aggregate = {
        "faithfulness": _mean("faithfulness"),
        "answer_relevancy": _mean("answer_relevancy"),
        "context_precision": _mean("context_precision"),
        "context_recall": _mean("context_recall"),
        "case_count": len(per_case),
    }

    print(json.dumps({"cases": per_case, "aggregate": aggregate}, default=str))


if __name__ == "__main__":
    main()
