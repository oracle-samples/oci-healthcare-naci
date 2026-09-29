# LiteLLM intelligent routing and cost management with OCI Generative AI

LiteLLM gives OCI Generative AI customers a central place to publish approved
model groups, select among deployments, return normalized usage, and grow into
team-level cost governance. This guide separates what the database-free
reference Gateway enforces from features that need a stateful LiteLLM Gateway
deployment. That distinction matters: adding `max_budget` without the documented
spend database does not create an enforceable dollar limit.

## What this reference deploys

The application runs the **LiteLLM AI Gateway** process. Its Gateway Router gives
clients two stable model groups:

| Alias | Default OCI model | Policy |
|---|---|---|
| `basic` | `google.gemini-2.5-flash-lite` | Recommended application default for routine text work |
| `reasoning` | `xai.grok-4.3` | Explicit escalation for difficult reasoning |

LiteLLM normalizes both OCI models behind the same OpenAI-style request and
response contract. A custom Gateway callback supplies a renewable OCI
instance-principal signer on each call, so customers do not put OCI user API
keys in the VM or configuration. LiteLLM's built-in Presidio integration calls
the internal analyzer and anonymizer before the Router and after the model,
which keeps the privacy boundary the same for both groups.

The current implementation deliberately makes tier choice visible to the
caller. It does not silently upgrade `basic` requests to the more expensive
model. It also applies these bounded-cost behaviors:

- Clients must select `basic` or `reasoning`; omitting `model` does not silently
  spend against an unintended tier.
- Default and maximum output limits are 512 tokens for `basic` and 4,096 for
  `reasoning`; callers may lower them.
- At most four model requests run concurrently and the admission queue is set
  to zero, so excess work is rejected instead of accumulating without bound.
- LiteLLM retries are set to zero, avoiding accidental duplicate inference
  spend. OCI can throttle on-demand requests dynamically, so a production client
  should use a bounded backoff policy with an overall attempt/cost limit.
- Response caching is disabled to avoid retaining clinical text. This favors
  privacy and simplicity over the cost savings a carefully governed cache could
  provide.
- The response includes provider-reported token usage when available. The
  reference does not persist prompts, responses, spend rows, or token counters.

These controls limit individual requests and concurrency. They are **not a hard
dollar budget**.

## LiteLLM Router capabilities

A LiteLLM model group can contain several deployments behind one logical name.
The Router can then select among healthy deployments with strategies including
weighted shuffle, least busy, usage based, lowest latency, and lowest configured
cost. Pre-call checks can account for deployment RPM, TPM, and maximum parallel
requests. Cooldowns, retries, fallbacks, and session affinity can improve
resilience when several equivalent deployments exist.

That pattern fits OCI Generative AI well:

1. LiteLLM provides one client contract and chooses a model or deployment tier.
2. OCI authenticates the workload with an instance principal and authorizes chat
   access in the selected compartment.
3. OCI serves the selected on-demand model and bills the tenancy.
4. LiteLLM converts the provider response back to the stable client shape.

Cost-based deployment routing is useful when a logical model group has multiple
equivalent endpoints with accurate prices. Configure explicit input/output
prices when LiteLLM's catalog does not contain the deployed OCI model. Do not
assume a generic token price reproduces the OCI bill: Oracle documents current
on-demand chat billing in characters, while LiteLLM's custom router prices are
expressed per token. Treat the OCI bill and Cost Analysis as the billing source
of truth.

## Intelligent tier routing

LiteLLM's **Auto Router** is an optional Gateway add-on. A client sends one
logical model name; a classifier maps the request to tiers such as `SIMPLE`,
`MEDIUM`, `COMPLEX`, or `REASONING`. Current options include heuristic, LLM,
keyword, and custom classifiers, plus context-window escalation, tier-specific
output limits, session behavior, and savings reporting.

For this OCI design, those tiers could map to approved OCI model aliases, for
example Flash-Lite for `SIMPLE` and Grok for `REASONING`. Before enabling that in
a healthcare workload:

- evaluate routing decisions on approved, representative synthetic or
  de-identified traffic;
- fail to an approved route if classification fails;
- keep classification inside the same redaction boundary;
- restrict every tier to approved models and processing locations;
- compare quality, false escalation, latency, and actual OCI cost; and
- use shadow evaluation before letting automated decisions affect production.

Auto Router is not enabled in this reference. The included
[`cost_controlled_chat.py`](../examples/cost_controlled_chat.py) demonstrates a
transparent local heuristic for evaluation without claiming to reproduce the
Auto Router. It defaults to `basic`, escalates only on simple visible rules,
limits frontier calls per UTC day, caps output, and stores aggregate counters
only.

OCI also offers a **smart model router** for routing one on-demand model across a
user-selected set of regions in the same realm. That solves a different problem:
capacity and regional placement for a fixed model. LiteLLM intelligent routing
chooses a model/tier based on the request. The current stack uses one fixed OCI
region and does not create an OCI routing profile. A production extension can
combine the layers after verifying LiteLLM support for OCI routing profiles and
the customer's approved regional boundary.

## Authoritative budgets and rate limits

For shared enforcement across users, teams, or applications, extend this
Gateway with virtual keys and a spend database. LiteLLM documents:

- dollar budgets at supported key, user, team, and other scopes;
- RPM, TPM, per-model, and maximum-parallel-request limits;
- usage and spend reporting by key/team/customer; and
- optional fail-closed budget enforcement when spend cannot be verified.

LiteLLM budgets require PostgreSQL. A database-free Gateway does not
enforce `max_budget`; the documented behavior is to continue serving requests.
Redis is used for efficient shared counters in multi-worker deployments, and
fail-closed enforcement adds an authoritative database check when a hard ceiling
is required. Feature availability and license requirements vary by scope, so
check the documentation for the version being deployed.

OCI Budgets complement this layer by alerting on compartment or tag spend. OCI
describes them as soft limits, evaluated periodically, so an alert is not a
real-time inference cutoff. A practical control stack is:

| Layer | Purpose |
|---|---|
| Application | Choose `basic` by default; require a reason to escalate; cap output |
| LiteLLM Gateway | Enforce virtual-key/team budgets and RPM/TPM limits; track spend |
| OCI IAM | Allow only approved GenAI actions, compartments, and optionally model OCIDs |
| OCI Billing | Budget alerts, cost reporting, and final billed-cost reconciliation |

## Examples

Open the SSH tunnel and export `GATEWAY_API_KEY` as shown in the README.

Run the end-to-end synthetic validation:

```bash
python3 examples/smoke_test.py
```

Use the low-cost route with a smaller output cap:

```bash
printf '%s\n' 'Summarize this synthetic workflow in three bullets.' | \
  python3 examples/cost_controlled_chat.py --mode basic --max-output-tokens 256
```

Let the transparent example policy select a route, while allowing at most two
successful reasoning calls during the current UTC day:

```bash
printf '%s\n' 'Analyze the trade-offs in this synthetic scheduling design.' | \
  python3 examples/cost_controlled_chat.py \
    --mode auto \
    --max-reasoning-calls-per-day 2
```

The example ledger defaults to `.litellm-example-usage.json` and contains only
the date, route call counts, and returned token counts. Token counts are
informational and should not be converted directly into an OCI dollar amount.
This single-process example is not safe as a shared organizational budget; use
the database-backed Gateway for that purpose.

## References

- [LiteLLM Router and routing strategies](https://docs.litellm.ai/docs/routing)
- [LiteLLM Auto Router](https://docs.litellm.ai/docs/auto_router/)
- [LiteLLM budgets and rate limits](https://docs.litellm.ai/docs/proxy/users)
- [LiteLLM custom cost callbacks](https://docs.litellm.ai/docs/observability/custom_callback)
- [LiteLLM OCI provider](https://docs.litellm.ai/docs/providers/oci)
- [OCI on-demand inference billing](https://docs.oracle.com/en-us/iaas/Content/generative-ai/pay-on-demand.htm)
- [OCI Generative AI dynamic throttling](https://docs.oracle.com/en-us/iaas/Content/generative-ai/dynamic-throttling.htm)
- [OCI Budgets are soft limits](https://docs.oracle.com/en-us/iaas/Content/Billing/Concepts/budgetsoverview.htm)
- [OCI model-level inference IAM conditions](https://docs.oracle.com/en-us/iaas/Content/generative-ai/limit-model-access.htm)
- [OCI smart model router announcement](https://docs.oracle.com/en-us/iaas/releasenotes/generative-ai/regional-router.htm)
