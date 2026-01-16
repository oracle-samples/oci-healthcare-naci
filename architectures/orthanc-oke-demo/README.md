# End-to-end deployment of Orthanc on Oracle Kubernetes Engine(OKE)

This Terraform sample demonstrates the creation of the OCI infrastructure resources and the deployment of Orthanc on Oracle Kubernetes Engine(OKE).

The Terraform sample creates the following OCI infrastructure resources:
* VCN, Subnets, NAT Gateway, Internet Gateway, Service Gateway, Security Lists and Route Tables.
* Oracle Kubernetes Engine(OKE) and node pools
* OCI Database with PostgreSQL
* Object Storage bucket
* IAM policies
* Virtual machine used for testing and debugging purpose only.

Orthanc is deployed to the OKE cluster through the supplied Helm chart.

## Getting Started

### Prerequisites
* Access to an OCI tenancy.
* A compartment where you can create OCI resources.

### How to run this demo
* Clone the repo
```
git clone <repo>
``` 
* Modify `terraform.tfvars` with appropriate values
* Run terraform
```
terraform init
terraform plan
terraform apply
``` 
* Get the Load Balancer IP and browse to it
```
http://<lb_ip>
```

## References
* [Oracle Cloud Infrastructure](https://docs.oracle.com/en-us/iaas/Content/GSG/Concepts/baremetalintro.htm)
* [Terraform OCI Provider](https://registry.terraform.io/providers/oracle/oci/latest/docs)
* [OCI Object Storage](https://docs.oracle.com/en-us/iaas/Content/Object/home.htm)
* [OCI Database with PostgreSQL](https://docs.oracle.com/en-us/iaas/Content/postgresql/home.htm)
* [Oracle Kubernetes Engine (OKE)](https://docs.oracle.com/en-us/iaas/Content/ContEng/home.htm)
* [Orthanc Server](https://www.orthanc-server.com/)
