data "google_project" "current" {
  project_id = var.project_id
}

locals {
  required_apis = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
  ])

  # Secret NAMES ONLY. Values never enter Terraform state.
  backend_secret_ids = toset(
    keys(
      jsondecode(
        file("${path.module}/../app/backend_secret_env.production.json")
      )
    )
  )
}

resource "google_project_service" "apis" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "tf_state" {
  name                        = var.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.artifact_repository
  description   = "CARDINAL frontend/backend deployment images"
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket" "model" {
  name                        = var.model_bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "backend" {
  for_each  = local.backend_secret_ids
  secret_id = each.value
  project   = var.project_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "backend_runtime" {
  account_id   = "cardinal-backend-runtime"
  display_name = "CARDINAL backend Cloud Run runtime"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "model_runtime" {
  account_id   = "cardinal-gemma-runtime"
  display_name = "CARDINAL Gemma Cloud Run runtime"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "github_deployer" {
  account_id   = "github-cardinal-deployer"
  display_name = "GitHub Actions CARDINAL deployer"

  depends_on = [google_project_service.apis]
}

resource "google_service_account" "cloudbuild_runtime" {
  account_id   = "cardinal-cloudbuild"
  display_name = "CARDINAL user-managed Cloud Build runtime"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "github_roles" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/compute.networkAdmin",
    "roles/monitoring.editor",
    "roles/run.admin",
    "roles/secretmanager.viewer",
    "roles/serviceusage.serviceUsageConsumer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_use_backend_sa" {
  service_account_id = google_service_account.backend_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_use_model_sa" {
  service_account_id = google_service_account.model_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_storage_bucket_iam_member" "github_state" {
  bucket = google_storage_bucket.tf_state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_storage_bucket_iam_member" "model_reader" {
  bucket = google_storage_bucket.model.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.model_runtime.email}"
}

resource "google_project_iam_member" "backend_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.backend_runtime.email}"
}

resource "google_project_iam_member" "cloudbuild_roles" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.builder",
    "roles/logging.logWriter",
    "roles/secretmanager.secretAccessor",
    "roles/storage.objectUser",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.cloudbuild_runtime.email}"
}

resource "google_service_account_iam_member" "bootstrap_operator_use_cloudbuild_sa" {
  count = var.bootstrap_operator_email == "" ? 0 : 1

  service_account_id = google_service_account.cloudbuild_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "user:${var.bootstrap_operator_email}"
}

resource "google_storage_bucket_iam_member" "cloudbuild_model_writer" {
  bucket = google_storage_bucket.model.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.cloudbuild_runtime.email}"
}

resource "google_project_iam_member" "serverless_network_user" {
  project = var.project_id
  role    = "roles/compute.networkUser"
  member  = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = var.github_pool_id
  display_name              = "GitHub CARDINAL"
  description               = "GitHub Actions federation for CARDINAL deployment"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.github_provider_id
  display_name                       = "${var.github_owner}/${var.github_repository}"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"              = "assertion.ref"
  }

  attribute_condition = "assertion.repository == '${var.github_owner}/${var.github_repository}' && assertion.ref == 'refs/heads/${var.github_deploy_branch}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com/"
  }
}

resource "google_service_account_iam_member" "github_federation" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repository}"
}

resource "google_billing_budget" "cardinal" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "CARDINAL Cloud Run safety budget"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.8
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.apis]
}
