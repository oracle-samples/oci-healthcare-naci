# End-to-end deployment of Orthanc on Oracle Kubernetes Engine(OKE)

This Terraform sample demonstrates the creation of the OCI infrastructure resources needed for Orthanc deployment and the deployment of Orthanc on Oracle Kubernetes Engine(OKE).

This demo is split into two Terraform root modules:

- `infra/`: OCI networking + OKE + Object Storage + PostgreSQL + test VM(for testing and debugging)
- `apps/`: Kubernetes/Helm resources (DB init job, Orthanc config secret, Helm release)

Orthanc is deployed to the OKE cluster through the supplied Helm chart.

## Getting Started

### Prerequisites
* Access to an OCI tenancy.
* A compartment where you can create OCI resources.

### How to run this demo
1) **Clone the repo**
```bash
git clone <repo>
``` 

2) **Change directory to `infra`**
```bash
cd infra
```

3) **Modify `terraform.tfvars` with appropriate values**
4) **Run Terraform**
```bash
terraform init
terraform plan
terraform apply
``` 
5) **Change directory to `apps`**
```bash
cd ../apps
```
6) **Run Terraform**
```bash
terraform init
terraform plan
terraform apply
``` 
7) **Get the Load Balancer's public IP and browse to it**
```
http://<lb_public_ip>
```

## Notes
- `apps/` reads infra outputs from `../infra/terraform.tfstate` via `terraform_remote_state` (local backend).
- The Kubernetes + Helm providers are configured from OKE kubeconfig *content* (no kubeconfig file required).
- `s3_secret_key` is marked sensitive and is stored in Terraform state; treat state as a secret.

## References
* [Oracle Cloud Infrastructure](https://docs.oracle.com/en-us/iaas/Content/GSG/Concepts/baremetalintro.htm)
* [Terraform OCI Provider](https://registry.terraform.io/providers/oracle/oci/latest/docs)
* [OCI Object Storage](https://docs.oracle.com/en-us/iaas/Content/Object/home.htm)
* [OCI Database with PostgreSQL](https://docs.oracle.com/en-us/iaas/Content/postgresql/home.htm)
* [Oracle Kubernetes Engine (OKE)](https://docs.oracle.com/en-us/iaas/Content/ContEng/home.htm)
* [Orthanc Server](https://www.orthanc-server.com/)
