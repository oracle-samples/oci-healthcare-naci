# OCI Healthcare and Life Sciences (HCLS) Engineering Repository

This repository provides production-ready reference architectures, automation, and sample implementations to accelerate secure, compliant solutions on Oracle Cloud Infrastructure (OCI) for healthcare and life sciences workloads. It is designed to help teams build HIPAA- and HITRUST-aligned foundations, modern data platforms, and MLOps-enabled applications with repeatable, infrastructure-as-code patterns.

**What you’ll find**

* Reference architectures: Clinically oriented data ingestion, analytics, and interoperability patterns (e.g., FHIR-enabled APIs, Object Storage and Autonomous Database, and more).
* Automation: Terraform modules and example stacks for VCN/compartments, IAM, Vault, WAF/NSGs, API Gateway, OKE, Container Registry, Observability, and cost governance.
* Application blueprints: Secure microservices on OKE/Functions, event-driven integration, and sample pipelines for CI/CD and DevSecOps with policy-as-code guardrails.
* MLOps patterns: Portable workflows for model training/serving on OCI (e.g., Data Science, Data Flow, OKE), with guidance for data lineage, monitoring, and model governance.


## Prerequisites

* Have an OCI Tenancy. Need a OCI Tenancy? Sign up for a free Oracle Cloud Tenant at https://signup.oraclecloud.com/
* After logging into a OCI tenancy for the first time, it is best practice to create a user for development. Get familiar with OCI IAM: https://docs.oracle.com/en-us/iaas/Content/Identity/users/create-user-accounts.htm
* Generate SSH keys for your new user: https://docs.oracle.com/en/learn/generate_ssh_keys/index.html

## Reference Architectures

* Mirth Connect on OCI VMs: [architectures/mirth-demo](./architectures/mirth-demo)
* Orthanic Demo on OCI: [architectures/orthanc-demo](./architectures/orthanc-demo)

## Help and Maintainers

* NACIE Healthcare and Life Sciences Engineering Team (Email:@oracle.com)

## Contributing

This project welcomes contributions from the community. Before submitting a pull request, please [review our contribution guide](./CONTRIBUTING.md)

## Security

Please consult the [security guide](./SECURITY.md) for our responsible security vulnerability disclosure process

## License

Copyright (c) 2026 year Oracle and/or its affiliates.

Released under the Universal Permissive License v1.0 as shown at
<https://oss.oracle.com/licenses/upl/>. [UPL](./LICENSE)