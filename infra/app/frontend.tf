resource "google_cloud_run_v2_service" "frontend" {
  name                 = var.frontend_service_name
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_ALL"
  invoker_iam_disabled = true
  deletion_protection  = false

  # Service-level ceiling protects the project even during revision transitions.
  # Permanent safe baseline: Terraform apply always parks this service.
  # START_DEMO temporarily changes it to automatic scale-to-zero.
  # Permanent safe baseline: Terraform apply always parks this service.
  # START_DEMO temporarily changes it to automatic scale-to-zero.
  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = 0
  }

  template {
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.frontend_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12

        http_get {
          path = "/healthz"
          port = 8080
        }
      }

      liveness_probe {
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }
}
