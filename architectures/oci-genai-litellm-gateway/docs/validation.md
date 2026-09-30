# Validation

Verified **2026-09-29**, using synthetic data only.

| Check | Result |
|---|---|
| Python suite | 70 passed; one upstream Starlette test-client deprecation warning |
| Installed development dependencies | `pip check`: no broken requirements |
| Terraform formatting and validation | Passed; OCI provider 8.29.0 |
| Cloud-init | Rendered gzip/base64 payload parsed; embedded sources round-tripped; within OCI metadata limit |
| Resource Manager source ZIP | Accepted by OCI; Terraform 1.5.x plan and Apply succeeded |
| Live infrastructure | Nine resources created in an isolated Ashburn compartment; E5 Flex, 1 OCPU, 16 GB RAM |
| Container builds/startup | LiteLLM Gateway 1.103.0 and Presidio images built on the VM; both containers healthy |
| Authentication | Missing bearer token returned HTTP 401 |
| Gateway Router | Authenticated model discovery returned exactly the `basic` and `reasoning` model groups |
| Request contract | Unsupported tool fields returned HTTP 422 without echoing submitted text |
| Presidio integration | Email, MRN, and DOB values were replaced by typed placeholders in both live model responses |
| `basic` | Successful OCI Gemini 2.5 Flash-Lite answer through the LiteLLM Gateway Router, with usage returned |
| `reasoning` | Successful OCI Grok 4.3 answer through the LiteLLM Gateway Router, with reasoning-token usage returned and reasoning traces removed |
| OCI identity | Both deployed model calls used the VM instance principal; no user API key on the VM |
| Fault injection | Stopping Presidio caused the Gateway to return HTTP 500 without forwarding the submitted identifier; service restarted and health recovered |
| Logging check | Known synthetic identifiers absent from both container logs after the smoke test |
| Secret handling | VM-generated gateway bearer token absent from Resource Manager Terraform state |
| Packaging | Deterministic ZIP; explicit source allowlist; state, credentials, test-tenancy configuration, and local artifacts excluded |

The first local provisioning attempt exposed that E4 was unavailable for the
chosen image and availability domain. The reference now defaults to E5 and checks
shape compatibility before launch. Those partial local resources were removed;
the successful live test was deployed from the ZIP through **OCI Resource Manager**.

The tests exercise actual Presidio/spaCy recognition, LiteLLM's standard
Presidio HTTP contracts, mandatory scans of all message roles, both model
groups, output scanning, failure paths, unsupported input surfaces, payload
limits, signer refresh behavior, and package isolation. A separate local check
started the real Gateway process and verified liveliness, HTTP 401 without a
key, authenticated model discovery, and pre-call callback enforcement. The
cloud smoke test additionally exercises the deployed Gateway Router,
containers, IAM policy, instance principal, both OCI model calls, and SSH tunnel
together.

These are functional reference checks, not a clinical privacy evaluation,
security certification, load test, or high-availability test. Presidio can miss
identifiers and over-redact useful information. A successful synthetic test does
not establish suitability for a customer's real PHI. Regional model availability,
processing location, terms, and pricing must be reviewed for each deployment.
