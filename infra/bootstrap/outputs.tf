output "project_number" {
  value = data.google_project.current.number
}

output "state_bucket" {
  value = google_storage_bucket.tf_state.name
}

output "artifact_repository" {
  value = google_artifact_registry_repository.app.repository_id
}

output "model_bucket" {
  value = google_storage_bucket.model.name
}

output "backend_runtime_service_account" {
  value = google_service_account.backend_runtime.email
}

output "model_runtime_service_account" {
  value = google_service_account.model_runtime.email
}

output "cloudbuild_runtime_service_account" {
  value = google_service_account.cloudbuild_runtime.email
}

output "github_deployer_service_account" {
  value = google_service_account.github_deployer.email
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}
