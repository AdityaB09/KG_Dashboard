resource "google_cloud_run_v2_service" "backend" {
  name                 = var.backend_service_name
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true
  deletion_protection  = false

  # Current SMART token/session stores are process-memory dictionaries.
  # Keep the entire service globally capped at one instance until shared
  # session persistence is introduced.
  scaling {
    max_instance_count = 1
  }

  template {
    service_account                  = var.backend_runtime_service_account
    max_instance_request_concurrency = 40
    timeout                          = "3600s"

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.backend_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.backend_env
        content {
          name  = env.key
          value = tostring(env.value)
        }
      }

      dynamic "env" {
        for_each = local.backend_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 18

        http_get {
          path = "/health"
          port = 8080
        }
      }

      liveness_probe {
        timeout_seconds   = 5
        period_seconds    = 30
        failure_threshold = 3

        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }
}
