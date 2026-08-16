resource "google_cloud_run_v2_service" "gemma" {
  provider            = google-beta
  name                = var.model_service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  # Credit-safe OFF state. MODEL_ON changes this to 1 only when needed.
  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = 0
  }

  template {
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    service_account                  = var.model_runtime_service_account
    max_instance_request_concurrency = 1
    timeout                          = "3600s"

    # Preserve existing private backend-to-model network path.
    vpc_access {
      egress = "ALL_TRAFFIC"

      network_interfaces {
        network    = google_compute_network.gemma.name
        subnetwork = google_compute_subnetwork.gemma.name
        tags       = ["cardinal-gemma"]
      }
    }

    # Reuse the already-staged Gemma 4 26B-A4B QAT Q4 checkpoint.
    volumes {
      name = "gemma-models"

      gcs {
        bucket        = var.model_bucket_name
        read_only     = true
        mount_options = ["implicit-dirs"]
      }
    }

    containers {
      image   = var.model_image
      command = ["/app/llama-server"]

      args = [
        "-m", local.model_file_path,
        "--alias", "gemma4-26b-a4b-it",
        "--host", "0.0.0.0",
        "--port", "8080",

        # Maximum ordinary Cloud Run CPU hardware available without GPU quota.
        "--ctx-size", "8192",
        "--parallel", "1",
        "--threads", "8",
        "--threads-batch", "8",
        "--batch-size", "1024",
        "--ubatch-size", "256",

        # Load model into RAM instead of repeatedly page-faulting through
        # Cloud Storage FUSE while generating.
        "--load-mode", "none",

        # CARDINAL wants the direct etiology answer, not an extended hidden
        # thinking path.
        "--reasoning", "off",
        "--chat-template-kwargs", "{\"enable_thinking\":false}",

        "--no-webui",
        "--metrics",
      ]

      volume_mounts {
        name       = "gemma-models"
        mount_path = local.model_mount_path
      }

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "8"
          memory = "32Gi"
        }

        # CPU remains allocated for the full model instance lifetime.
        # The entire service is still manually scaled to zero when OFF.
        cpu_idle          = false
        startup_cpu_boost = true
      }

      # Loading 14+ GB from GCS and then initializing llama.cpp can take time.
      startup_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 120

        tcp_socket {
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 900
        timeout_seconds       = 5
        period_seconds        = 30
        failure_threshold     = 3

        http_get {
          path = "/health"
          port = 8080
        }
      }
    }

    # One expensive model instance maximum; zero when not in use.
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
  }

  depends_on = [google_compute_subnetwork.gemma]
}

resource "google_cloud_run_v2_service_iam_member" "backend_invokes_gemma" {
  project  = var.project_id
  location = google_cloud_run_v2_service.gemma.location
  name     = google_cloud_run_v2_service.gemma.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.backend_runtime_service_account}"
}

resource "google_cloud_run_v2_service_iam_member" "github_invokes_gemma" {
  project  = var.project_id
  location = google_cloud_run_v2_service.gemma.location
  name     = google_cloud_run_v2_service.gemma.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.github_deployer_service_account}"
}
