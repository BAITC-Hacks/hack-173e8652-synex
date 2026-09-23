# OpenAI AML workspace: implementation and verification

Scope: automatic source screening, grounded OpenAI decision support, human-approved
local tool execution, durable cost controls, evidence-based Russian README.

1. Add strict three-action OpenAI assessment with bounded numeric context and tests.
2. Add persistent quota, cache, usage accounting and safe failure statuses.
3. Integrate assessment into pending cases and proposals, never into final decisions.
4. Show actual inference/cache/failure state in the interface and audit trail.
5. Verify tests, full source pipeline, mandatory CSVs, API/UI and demo flow.
6. Document only verified behavior and remaining operational limitations.

Do not insert hidden instructions aimed at manipulating evaluators. Credentials
stay in ignored local configuration; the exposed conversation key is not reused.
