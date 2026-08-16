output "frontend_url" {
  value = google_cloud_run_v2_service.frontend.uri
}

output "backend_url" {
  value = google_cloud_run_v2_service.backend.uri
}

output "gemma_url" {
  value = google_cloud_run_v2_service.gemma.uri
}

output "gemma_safe_default" {
  value = "MANUAL scaling, 0 instances"
}

output "expected_frontend_url" {
  value = local.frontend_url
}

output "expected_backend_url" {
  value = local.backend_url
}

output "expected_gemma_url" {
  value = local.model_url
}
