############################################################
# Create a light weight Linux VM on the public subnet 
# for testing/debugging only
############################################################
locals {
  public_vm_cloud_init = <<-EOF
    #cloud-config
    package_update: true
    package_upgrade: false

    packages:
      - yum-utils
      - python3
      - python3-pip

    runcmd:
      # ---- Docker CE ----
      - yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
      - yum install -y docker-ce docker-ce-cli containerd.io
      - systemctl enable docker
      - systemctl start docker
      - usermod -aG docker opc

      # ---- OCI CLI ----
      - pip3 install --upgrade pip
      - pip3 install oci-cli

      # ---- PostgreSQL client ----
      - dnf install -y postgresql

      # ---- Convenience ----
      - echo 'export PATH=$PATH:/usr/local/bin' >> /home/opc/.bashrc
      - chown -R opc:opc /home/opc
  EOF
}

resource "oci_core_instance" "test_vm" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad_name
  display_name        = "public-test-vm"
  shape               = "VM.Standard.E5.Flex"

  shape_config {
    ocpus         = 1
    memory_in_gbs = 8
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public_subnet.id
    assign_public_ip = true
    display_name     = "public-vnic"
  }

  source_details {
    source_type = "image"
    source_id   = var.test_vm_image_ocid
  }

  metadata = {
    ssh_authorized_keys = local.ssh_public_key
    user_data = base64encode(local.public_vm_cloud_init)
  }
}
