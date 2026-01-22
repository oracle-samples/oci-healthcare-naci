############################################################
# OCI PostgreSQL DB System
############################################################

resource "oci_psql_db_system" "psql" {
  compartment_id = var.compartment_ocid
  display_name   = "demo-orthanc-db"
  db_version     = var.psql_db_version
  shape          = var.psql_shape

  credentials {
    # username = "admin"
    username = var.psql_admin_username

    password_details {
      password_type = "PLAIN_TEXT"
      password      = var.psql_admin_password
    }
  }

  network_details {
    subnet_id = oci_core_subnet.private_subnet.id
  }

  storage_details {
    is_regionally_durable = true
    system_type           = "OCI_OPTIMIZED_STORAGE"
  }

  instance_count              = 1
  instance_ocpu_count         = var.psql_ocpus
  instance_memory_size_in_gbs = var.psql_memory_gbs

  system_type = "OCI_OPTIMIZED_STORAGE"
}

data "oci_psql_db_system_connection_detail" "psql_conn" {
  db_system_id = oci_psql_db_system.psql.id
}


############################################################
