-- Scoped data fix for one demo document (long_contract_with_pricing_schedule.pdf,
-- document_id e4ba418a-cc5d-4608-856c-2a1f679c0779... see WHERE clauses below).
-- Ingestion had stored this contract's indemnification, liability_cap and
-- pricing clauses twice: once complete, once truncated mid-sentence. The three
-- playbook entries for this document happened to attach to the truncated
-- clause_id, so current_language read as a broken fragment ("4. Indemnif").
-- This replaces current_language with the full clause text and rewrites the
-- fallback ladder / redline to name the real parties (Vendor Company K Pvt.
-- Ltd. as Client, Vendor Company L Pvt. Ltd. as Vendor) instead of "either
-- Party". Touches only rows scoped to this one document_id; no other document,
-- table structure or application code changes.

BEGIN;

-- Indemnification: full clause text, and the fallback list previously repeated
-- the truncated fragment as a fake third option -- replaced with a real ladder.
UPDATE playbook_entry
SET current_language = '4. Indemnification. Each Party shall indemnify, defend, and hold harmless the other Party from any third-party claims arising from the indemnifying Party''s gross negligence or willful misconduct in the performance of its obligations under this Agreement.',
    fallback_positions = '["Mutual indemnification limited to gross negligence or willful misconduct, as currently drafted.", "Extend Vendor Company L''s indemnification to cover ordinary negligence, not only gross negligence or willful misconduct.", "Accept the clause as drafted, provided Vendor Company L carries minimum liability insurance of INR 1 crore naming Vendor Company K as an additional insured."]',
    suggested_redline = '4. Indemnification. Vendor Company L Pvt. Ltd. shall indemnify, defend, and hold harmless Vendor Company K Pvt. Ltd. from any third-party claims arising from Vendor Company L''s negligence (not limited to gross negligence) or willful misconduct in the performance of its obligations under this Agreement. Vendor Company L shall maintain commercial general liability insurance of not less than INR 1,00,00,000 naming Vendor Company K as an additional insured.'
WHERE playbook_entry_id = '1f7c99e3-1a00-4aa6-8ce5-f21dc2f9e37a';

-- Liability cap: full clause text; fallback ladder already named real terms
-- (2x fees, 12 months) so only current_language and the redline get the
-- party names added for clarity.
UPDATE playbook_entry
SET current_language = '3. Limitation of Liability. Except for breaches of the Confidentiality clause, in no event shall either Party''s aggregate liability arising out of or related to this Agreement exceed the total fees paid in the twelve (12) months preceding the claim.',
    fallback_positions = '["Cap Vendor Company L''s aggregate liability at two (2) times the total fees paid by Vendor Company K in the twelve (12) months preceding the claim.", "Cap aggregate liability at the total fees paid in the twelve (12) months preceding the claim, with standard carve-outs for confidentiality, IP infringement, and gross negligence.", "Cap aggregate liability at a fixed amount of INR 50,00,000, aligned to the contract''s annual value under Schedule A."]',
    suggested_redline = 'In no event shall either Party''s total aggregate liability arising out of or related to this Agreement exceed two (2) times the total fees paid by Vendor Company K to Vendor Company L under this Agreement in the twelve (12) months preceding the claim, except for breaches of the Confidentiality clause, which remain uncapped.'
WHERE playbook_entry_id = 'f1395daf-26f2-4894-909d-587d7d0c9b9d';

-- Annual price adjustment cap: full clause text; fallback ladder already had
-- concrete percentages (5%, 7%, 10%) and a notice period, so only
-- current_language and the redline get Vendor Company L named as the party
-- who must give notice.
UPDATE playbook_entry
SET current_language = '7. Annual Review. The prices in Schedule A shall be reviewed annually and may be adjusted by mutual written agreement of the Parties, provided that no single annual adjustment shall exceed ten percent (10%) of the then-current price for any line item.',
    fallback_positions = '["Cap any single annual adjustment at five percent (5%) to protect Vendor Company K against rapid cost inflation.", "Cap annual adjustments at the Consumer Price Index (CPI) increase, not to exceed seven percent (7%).", "Maintain Vendor Company L''s ten percent (10%) annual cap but require ninety (90) days advance written notice and detailed cost justification before any increase takes effect."]',
    suggested_redline = '7. Annual Review. The prices in Schedule A shall be reviewed annually and may be adjusted by mutual written agreement of the Parties, provided that no single annual adjustment shall exceed five percent (5%) of the then-current price for any line item, and Vendor Company L shall provide at least sixty (60) days prior written notice of any proposed price adjustment to Vendor Company K.'
WHERE playbook_entry_id = '1e82e8df-59f1-4c97-8a85-6e5495788676';

-- Same fix mirrored onto the clause rows the flags point at, so the "Current
-- Language" panel above each playbook entry (which reads from Clause, not
-- PlaybookEntry) shows the same full text instead of the truncated fragment.
UPDATE clause SET extracted_text = '4. Indemnification. Each Party shall indemnify, defend, and hold harmless the other Party from any third-party claims arising from the indemnifying Party''s gross negligence or willful misconduct in the performance of its obligations under this Agreement.'
WHERE clause_id = 'fd9c48e2-22f1-4d14-9d37-caf77b405530';

UPDATE clause SET extracted_text = '3. Limitation of Liability. Except for breaches of the Confidentiality clause, in no event shall either Party''s aggregate liability arising out of or related to this Agreement exceed the total fees paid in the twelve (12) months preceding the claim.'
WHERE clause_id = '65ccb7e9-6cf2-4336-955a-856a6a818781';

UPDATE clause SET extracted_text = '7. Annual Review. The prices in Schedule A shall be reviewed annually and may be adjusted by mutual written agreement of the Parties, provided that no single annual adjustment shall exceed ten percent (10%) of the then-current price for any line item.'
WHERE clause_id = 'f2bbe054-335a-4d19-9750-26b8930244f0';

COMMIT;
