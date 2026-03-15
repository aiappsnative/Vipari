# Changelog

## 2026-03-15 — Detection engine and GitHub App flow hardened

### Added
- durable audit, finding, artifact, and comment persistence
- artifact lineage storage and baseline-aware reasoning support
- managed PR comment dedupe/update behavior
- compact TLDR plus collapsible detailed reviewer comment format
- regression tests for reviewer comment formatting behavior
- transient opened-PR diff retry handling on the webhook path
- private-key path resolution coverage and GitHub App JWT safety-window coverage

### Fixed
- GitHub App private key path resolution across server and worker contexts
- intermittent installation token failures caused by JWT expiration-window edge cases
- duplicate risk-level lines in detailed reviewer comments
- truncated TLDR summaries in reviewer comments
- live opened-PR app flow failures caused by short-lived GitHub diff propagation delays

### Verified
- end-to-end GitHub App bot-authored comment posting against `dummyAI`
- managed comment updates from `amit-ai-auditor-dev[bot]`
- live app-flow comment rendering with compact TLDR and collapsible details

### Product impact
- PromptDrift now behaves more like a durable AI change-audit system than a one-shot comment bot
- the system preserves history needed for future trend, baseline, and governance use cases

## 2026-03-13 — MVP end-to-end verified

### Added
- FastAPI webhook endpoint for GitHub `pull_request` events
- GitHub App JWT generation and installation token exchange
- Private pull request diff retrieval
- Azure OpenAI / Foundry-backed PR analysis
- PR comment publishing
- Local credential verification script
- `.env.example` environment template

### Fixed
- GitHub App JWT issuer handling
- Private repository diff fetching for authenticated GitHub App calls
- Azure-compatible model selection for live analysis

### Verified
- End-to-end webhook processing against private test repository `dummyAI`
- Bot comment posting by `amit-ai-auditor-dev[bot]`

### Known limitations
- Keyword-based drift detection only
- Synchronous processing
- Minimal automated test coverage
