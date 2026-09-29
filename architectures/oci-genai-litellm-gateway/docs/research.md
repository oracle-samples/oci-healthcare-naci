# Version and model research

Verified against primary sources on **2026-09-29**. This record describes source and documentation checks; the deployment's validation notes describe which live checks were actually run.

## LiteLLM

The current stable release is **1.103.0**. GitHub's latest-release endpoint reports `v1.103.0`, published September 28, with `prerelease: false`; PyPI reports the same version. Pin the release for reproducibility rather than resolving `latest` during each deployment.

- [GitHub release](https://github.com/BerriAI/litellm/releases/tag/v1.103.0)
- [GitHub latest-release metadata](https://api.github.com/repos/BerriAI/litellm/releases/latest)
- [PyPI metadata](https://pypi.org/pypi/litellm/json)
- [LiteLLM OCI provider documentation](https://docs.litellm.ai/docs/providers/oci)

## OCI model choices

| Route | OCI model ID | Intended use |
| --- | --- | --- |
| `basic` | `google.gemini-2.5-flash-lite` | Low-cost, low-latency simple text tasks. Oracle documents thinking as disabled for this model. |
| `reasoning` | `xai.grok-4.3` | Complex reasoning; documented by both Oracle and LiteLLM. Give reasoning enough output tokens: a small budget can produce no visible answer. |

Both are offered on demand in `us-ashburn-1`, `us-chicago-1`, and `us-phoenix-1`. **These models are accessed through OCI Generative AI but hosted externally:** Google Flash-Lite uses a Google Americas location for US requests; Grok calls reach xAI. Customers must assess those processing boundaries for their workload. Model availability, access requirements, limits, and prices remain tenancy- and time-dependent.

Oracle also lists the newer `xai.grok-4.7` (released September 23), with mandatory reasoning and configurable effort. The pinned LiteLLM provider transforms arbitrary `xai.*` model IDs using the generic OCI request format, but its published provider model list names Grok 4.3. Retain the documented default until a replacement passes a live smoke test. Old `xai.grok-4`, Grok 3, and Grok Fast variants retired August 15, 2026.

For an OCI-hosted alternative, Llama 4 Scout (`meta.llama-4-scout-17b-16e-instruct`) is on demand in Chicago. In Ashburn it requires dedicated capacity. This changes the cost and infrastructure assumptions.

- [Oracle model availability and external processing](https://docs.oracle.com/en-us/iaas/Content/generative-ai/model-endpoint-regions.htm)
- [Flash-Lite model details](https://docs.oracle.com/en-us/iaas/Content/generative-ai/google-gemini-2-5-flash-lite.htm)
- [Grok 4.3 model details](https://docs.oracle.com/en-us/iaas/Content/generative-ai/xai-grok-4-3.htm)
- [Grok 4.7 model details](https://docs.oracle.com/en-us/iaas/Content/generative-ai/x-ai-grok-4-7.htm)
- [Llama 4 Scout](https://docs.oracle.com/en-us/iaas/Content/generative-ai/meta-llama-4-scout.htm)
- [Retirement schedule](https://docs.oracle.com/en-us/iaas/Content/generative-ai/deprecating-on-demand.htm)
- [Current OCI prices](https://www.oracle.com/cloud/price-list/)

## Gateway integration details

The Gateway loads a custom pre-call callback from `gateway/oci_auth.py`. The callback passes the OCI signer on each Router call instead of placing it in `model_list`: Router initialization deep-copies that list, while instance-principal signers contain an uncopyable lock.

LiteLLM 1.103.0 invokes `oci_signer.do_request_sign`. OCI's instance-principal signer refreshes its token from `__call__`. The application adapter must therefore implement `do_request_sign` by calling the wrapped signer's public callable interface. Passing the raw instance-principal signer directly bypasses its normal token-refresh path.

LiteLLM Gateway's built-in Presidio guardrail is configured for both input and output. It calls the separately isolated service through Presidio's `/analyze` and `/anonymize` HTTP contract and fails closed when configured protection cannot verify the response. Payload logging, telemetry, verbose logs, raw request/response logs, and detailed errors are disabled. `redact_messages_in_exceptions` and `expose_router_debug_in_errors` are additional controls; they do not replace restricting logs and responses.

- [Pinned Router source](https://github.com/BerriAI/litellm/blob/v1.103.0/litellm/router.py)
- [LiteLLM Gateway Docker deployment](https://docs.litellm.ai/docs/proxy/docker_quick_start)
- [LiteLLM Presidio guardrail](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2)
- [Pinned OCI signing implementation](https://github.com/BerriAI/litellm/blob/v1.103.0/litellm/llms/oci/common_utils.py)
- [Oracle SDK token-refresh implementation](https://github.com/oracle/oci-python-sdk/blob/v2.187.1/src/oci/auth/signers/security_token_signer.py)
- [Pinned OCI request transformation](https://github.com/BerriAI/litellm/blob/v1.103.0/litellm/llms/oci/chat/transformation.py)
- [Pinned LiteLLM logging controls](https://github.com/BerriAI/litellm/blob/v1.103.0/litellm/__init__.py)
