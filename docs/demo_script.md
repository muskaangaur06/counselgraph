# Demo Script

A walkthrough covering the three scenarios the project is meant to demonstrate: a vendor agreement review with a high-risk flag, a missing-clause gap, and an escalation path. All three use the sample contracts already in `data/sample_contracts/`.

## Setup

1. Start the server: `uvicorn counsel_graph.api.main:app --port 8000` (from `src/`, or with `PYTHONPATH` pointed at `src/`).
2. Open `http://localhost:8000/ui`. You'll land on a login screen.
3. Sign in with the admin credentials from `.env` (`ADMIN_USERNAME`/`ADMIN_PASSWORD`, defaults to `admin` / `admin@321` if unchanged).

## Scenario 1: Vendor agreement first-pass review

Goal: show OCR-free text extraction, clause extraction with confidence, a high-risk flag with a recommended action, and a missing-clause gap, then approve it.

1. Go to the **Intake & Review** tab.
2. Upload `data/sample_contracts/sample_vendor_agreement.pdf`.
3. Fill in vendor name and contract name (optional), leave collection name blank.
4. Click **Submit for Review**.
5. Point out on screen:
   - The docket strip: number of clauses extracted, high-risk flags, conflicts, missing clauses.
   - The **Limitation of Liability** clause flagged high risk, with its confidence score and recommended action ("Escalate to senior counsel").
   - The missing-clause section, if the termination clause wasn't picked up as its own type this run (clause extraction is LLM-based and not perfectly deterministic; this is worth calling out honestly rather than hiding).
6. Set the reviewer decision to **Approve**, enter a reviewer name, click **Record Decision**.
7. Click **Export Review Summary** to download a plain-text record of the decision.
8. Copy the `job_id` shown in the response.

## Scenario 2: Policy document review

Goal: show a non-contract document (an internal policy) going through the same pipeline.

1. Upload `data/sample_contracts/sample_vendor_onboarding_policy.pdf` the same way.
2. Note that this document has no vendor/counterparty in the usual sense, so the extracted "parties" and "contract type" fields will look different from a contract, a policy document with escalation and mandatory-provision sections.

## Scenario 3: High-risk escalation flow

Goal: show the escalate path explicitly, since it's the governance mechanism that keeps this from being "AI makes a legal call."

1. Upload `data/sample_contracts/sample_mutual_nda.pdf`. This NDA was written with a short (1-year) confidentiality survival period and a foreign governing-law clause on purpose, both of which the risk taxonomy considers worth flagging.
2. When the review pauses, instead of Approve or Reject, select **Escalate to Senior Counsel**, add a comment explaining why (e.g. "Confidentiality survival period is below our 5-year policy floor"), and submit.
3. Go to the **Docket Log** tab, paste in the `job_id`, and click **Retrieve Docket**.
4. Point out the full audit trail: every processing stage logged in order, and the final `review_decision` entry showing `action=escalate` with the reviewer's name and comment.

## Scenario 4: Ask a question

Goal: show the query pipeline's two checkpoints and the revise loop.

1. Go to the **Ask Counsel** tab.
2. Enter the collection name from Scenario 1 (shown in that ingestion's `document_context`, or just the filename with the extension replaced by an underscore, e.g. `sample_vendor_agreement_pdf`).
3. Ask: "What is the limitation of liability clause, and does it cover data breach costs?"
4. At the evidence checkpoint, review the retrieved hits and the auditor's sufficiency verdict, then click **Proceed**.
5. At the answer checkpoint, read the drafted answer and its citations. To show the revise loop, pick **Revise**, add a comment like "Also mention the confidentiality carve-out," and submit; the system re-drafts using the same evidence plus your feedback.
6. On the second draft, click **Approve** to finalize.

## Scenario 5: Operations dashboard

Goal: show aggregate throughput and risk metrics across everything processed so far.

1. Go to the **Operations Dashboard** tab.
2. Point out the contract count, the risk-level breakdown, missing-clause and conflicting-clause counts, and the job-status breakdowns for both ingestion and query jobs.
3. Click **Refresh** after running another scenario to show the numbers update live.

## What to call out explicitly

- Nothing is final until a named human approves it. Every decision point is visible in the UI, not hidden behind a single "generate" button.
- Risk flags carry a confidence score and a recommended action, not just a severity label.
- The missing-clause check is a deterministic comparison against a checklist per contract type, not another LLM guess, so it's exact even when clause extraction itself has some variance run to run.
- The audit trail is append-only and covers every stage, not just the final decision.
