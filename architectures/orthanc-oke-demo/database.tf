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
# Initialize DB: create database "orthanc" and user "dicom"
# NOTE: Requires 'psql' installed where Terraform runs.
############################################################
resource "kubernetes_secret_v1" "psql_init" {
  metadata {
    name      = "psql-init-secret"
    namespace = "default"
  }

  data = {
    admin_username = var.psql_admin_username
    admin_password = var.psql_admin_password
    dicom_username = var.psql_dicom_username
    dicom_password = var.psql_dicom_password
  }
}

resource "kubernetes_job_v1" "init_orthanc_db" {
  depends_on = [
    oci_psql_db_system.psql,
    kubernetes_secret_v1.psql_init,
    oci_containerengine_node_pool.node_pool
  ]

  metadata {
    name      = "init-orthanc-db"
    namespace = "default"
  }

  spec {
    backoff_limit = 3

    template {
      metadata {}
      spec {
        restart_policy = "OnFailure"

        container {
          name  = "psql-init"
          image = "postgres:15"

          env {
            name = "PGHOST"
            value = data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].fqdn
          }
          env {
            name  = "PGPORT"
            value = tostring(data.oci_psql_db_system_connection_detail.psql_conn.primary_db_endpoint[0].port)
          }
          env {
            name  = "PGUSER"
            # value = "admin"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.psql_init.metadata[0].name
                key  = "admin_username"
              }
            }
          }
          env {
            name  = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.psql_init.metadata[0].name
                key  = "admin_password"
              }
            }
          }
          env {
            name = "DICOM_USERNAME"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.psql_init.metadata[0].name
                key  = "dicom_username"
              }
            }
          }
          env {
            name = "DICOM_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.psql_init.metadata[0].name
                key  = "dicom_password"
              }
            }
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            set -eu

            echo "Waiting for PostgreSQL..."
            until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER"; do sleep 5; done

            echo "Creating database/user (idempotent)..."
            psql -v ON_ERROR_STOP=1 \
                -d postgres \
                -v db_name="orthanc" \
                -v db_user="$DICOM_USERNAME" \
                -v db_pass="$DICOM_PASSWORD" <<'SQL'
            
            -- Create role if not exists
            SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_pass')
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = :'db_user'
            )
            \gexec

            SELECT format('GRANT %I TO %I', :'db_user', current_user)
            WHERE NOT EXISTS (
            SELECT 1
            FROM pg_auth_members m
            JOIN pg_roles r ON r.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE r.rolname = :'db_user'
                AND u.rolname = current_user
            )
            \gexec

            -- Create database if not exists
            SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
            WHERE NOT EXISTS (
                SELECT 1 FROM pg_database WHERE datname = :'db_name'
            )
            \gexec

            SQL
            EOT
            ]
        }
      }
    }
  }
}
