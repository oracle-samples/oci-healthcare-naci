# Deploying Orthanc on Oracle Cloud Infrastructure

This sample aims to demonstrate the deployment of Orthanc, a free and open-source DICOM server, on OCI and its integration with Object Storage and Database with PostgreSQL.

## Getting Started

### Dependencies

* A VM with the latest Docker engine. Oracle Linux 8 and Docker 26.1.3 were used to build this demo.
* An Object Storage bucket.
* A Database with PostgreSQL system node.
* Required IAM policies.

### Deployment
* Add the following IAM policies
```
# Create a dynamic group for Orthanc VM
All {instance.id = '<vm_ocid>'}

# Allow VM to access object storage bucket
allow dynamic-group <dynamic_group_name> to manage object-family in compartment <compartment_name> where target.bucket.name = '<object_storage_bucket_name>'
```

* Download Orthanc Docker image
```
docker pull orthancteam/orthanc
``` 
* Retrieve the `orthanc.json` file enclosed in the repo and modify as needed.

* Create and start container
```
docker run -p 4242:4242 -p 8042:8042 --rm -v ~/orthanc/orthanc.json:/etc/orthanc/orthanc.json:ro orthancteam/orthanc
```
* Open a browser and enter the following URL
```
http://<VM_IP>:8042/ui/app/#/
```

## Orthanc Configuration
The sample configuration file enclosed in this the repo, `orthanc.json`, contains the minimum settings needed to integrate Orthanc with OCI Object Storage bucket and OCI Database with PostgreSQL. Refer to [Orthanc Book](https://orthanc.uclouvain.be/book/index.html) for more details.

## References
* [Oracle Cloud Infrastructure](https://docs.oracle.com/en-us/iaas/Content/GSG/Concepts/baremetalintro.htm)
* [OCI Object Storage](https://docs.oracle.com/en-us/iaas/Content/Object/home.htm)
* [OCI Database with PostgreSQL](https://docs.oracle.com/en-us/iaas/Content/postgresql/home.htm)
* [Oracle Kubernetes Engine (OKE)](https://docs.oracle.com/en-us/iaas/Content/ContEng/home.htm)
* [Orthanc Server](https://www.orthanc-server.com/)
