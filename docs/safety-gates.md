# Safety gates

These are acceptance gates for the Luna boundary. “Deferred” means this stack
cannot claim runtime enforcement; the OpenShift/sibling repository must provide
it before production use.

| Rule | Observable pass | Fail/block |
|---|---|---|
| `confirm_understanding` consequential actions | External loop requests explicit confirmation before action | Any action proceeds without confirmation: block |
| Approved-events-only | Tool loop allowlist contains only event-approved tools | Unknown/unlisted tool is executed: block |
| `not_operating` honesty | UI/loop states when no live operation or data exists | A response claims live operation without evidence: fail |
| No mock-as-live | Fixtures and demos are labelled dummy/non-live | Mock output presented as live: block |
| Distress stop | Distress signal stops/suspends the loop and records the stop | Further tool/action call after distress: block |
| Counts, not percentages | Reports use integer counts and denominators | Unsupported percentages or inferred precision: fail |
| Dummy/no-PII scope | Config, examples, and smoke tests use dummy values and no PII | Credentials, tokens, or participant PII committed/logged: block |

The first six runtime gates are sibling-repo/OpenShift loop responsibilities,
deferred here. Luna enforces only network/key boundaries, credential redaction,
and translator’s no-tool-execution contract. Operator review must block launch
until the sibling loop supplies evidence for those gates.
