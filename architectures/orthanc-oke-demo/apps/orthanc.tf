############################################################
# Orthanc config + DB init job + Helm release (apps)
# This stack expects infra to be applied first.
############################################################

locals {
  psql_connection = format(
    "postgresql://%s:%s@%s:%d/orthanc?sslmode=prefer",
    var.psql_dicom_username,
    var.psql_dicom_password,
    data.terraform_remote_state.infra.outputs.postgres_primary_fqdn,
    data.terraform_remote_state.infra.outputs.postgres_primary_port
  )
}

############################################################
# JSON config file as required by Orthanc
############################################################

locals {

  orthanc_json = jsonencode({
    Name                  = "Demo Archive"
    HttpPort              = 8042
    DicomAet              = "ARCHIVE"
    DicomPort             = 104
    AuthenticationEnabled = false
    RemoteAccessAllowed   = true

    AwsS3Storage = {
      BucketName          = data.terraform_remote_state.infra.outputs.bucket_name
      Region              = var.region
      AccessKey           = data.terraform_remote_state.infra.outputs.s3_access_key_id
      SecretKey           = data.terraform_remote_state.infra.outputs.s3_secret_key
      Endpoint            = data.terraform_remote_state.infra.outputs.bucket_s3_endpoint
      VirtualAddressing   = false
    }

    PostgreSQL = {
      EnableIndex   = true
      EnableStorage = false
      Lock          = false
      ConnectionUri = local.psql_connection
    }

    Plugins = [
      "/usr/share/orthanc/plugins-available/libOrthancAwsS3Storage.so",
      "/usr/share/orthanc/plugins-available/libOrthancPostgreSQLIndex.so",
      "/usr/share/orthanc/plugins-available/libOrthancExplorer2.so",
      "/usr/share/orthanc/plugins-available/libOrthancGdcm.so",
      "/usr/share/orthanc/plugins-available/libOrthancDicomWeb.so"
    ]
  })
}

resource "kubernetes_secret_v1" "orthanc_json" {
  metadata {
    name      = "orthanc-json"
    namespace = "default"
  }

  binary_data = {
    "orthanc.json" = base64encode(local.orthanc_json)
  }

  type = "Opaque"
}
############################################################
# DB initialization (creates database "orthanc" and user "dicom")
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

resource "time_sleep" "wait_for_oke" {
  depends_on      = [data.oci_containerengine_cluster_kube_config.this]
  create_duration = "120s"
}

resource "kubernetes_job_v1" "init_orthanc_db" {
  depends_on = [
    time_sleep.wait_for_oke,
    kubernetes_secret_v1.psql_init
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
            name  = "PGHOST"
            value = data.terraform_remote_state.infra.outputs.postgres_primary_fqdn
          }
          env {
            name  = "PGPORT"
            value = tostring(data.terraform_remote_state.infra.outputs.postgres_primary_port)
          }
          env {
            name = "PGUSER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.psql_init.metadata[0].name
                key  = "admin_username"
              }
            }
          }
          env {
            name = "PGPASSWORD"
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

############################################################
# Helm release
############################################################

resource "helm_release" "orthanc" {
  depends_on = [
    kubernetes_secret_v1.orthanc_json,
    kubernetes_job_v1.init_orthanc_db
  ]

  name      = "orthanc"
  namespace = "default"

  chart = "${path.module}/helm/orthanc-oke-0.1.3.tgz"

  wait    = true
  timeout = 300
}
