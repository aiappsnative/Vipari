# AI Act Readiness FAQ

## What does DriftGuard help with?

DriftGuard helps teams track repository-level AI control surfaces, review baseline changes, preserve decision history, and assemble evidence packages for oversight and audit conversations.

## Does DriftGuard make us compliant with the EU AI Act?

No. DriftGuard supports readiness work such as logging, oversight, monitoring, traceability, and evidence preparation. It does not provide legal advice, legal classification, or a guarantee of compliance.

## What are provenance labels in DriftGuard exports?

Provenance labels explain what kind of output or artifact a reviewer is looking at.

- Review-output provenance labels distinguish deterministic fallback records from AI-assisted review narratives.
- Artifact provenance labels distinguish AI control surfaces, model/config surfaces, governance surfaces, and supporting repository artifacts.
- Baseline review records show human-reviewed decisions separately from system-generated evidence.

## Why does this matter?

Readiness work is easier when reviewers can tell whether a record is raw repository evidence, a human approval decision, or an AI-assisted narrative generated from deterministic evidence.

## What is included in the compliance export?

The export can include:

- approved baseline inventory
- baseline audit history and governance summary
- PR scan history and findings
- risk events and repo posture history
- optional raw artifact content tied to approved baselines or in-window PR scans

## What is not included?

The export is not a full repository archive and is not a legal certification package. Historical backfill content is excluded from raw artifact-content export output.

## How should teams use DriftGuard for AI governance?

Use it to maintain a reviewed baseline, monitor repository change over time, preserve reviewer decisions, and prepare evidence for internal or external oversight discussions.
