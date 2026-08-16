locals {
  frontend_url       = "https://${var.frontend_service_name}-${var.project_number}.${var.region}.run.app"
  backend_url        = "https://${var.backend_service_name}-${var.project_number}.${var.region}.run.app"
  model_url          = "https://${var.model_service_name}-${var.project_number}.${var.region}.run.app"
  model_gcs_location = "gs://${var.model_bucket_name}/${var.model_gcs_prefix}"
  model_mount_path   = "/models"
  model_file_path    = "${local.model_mount_path}/${var.model_gcs_prefix}/${var.model_filename}"

  source_backend_env = jsondecode(file("${path.module}/backend_env.production.json"))

  backend_env = merge(local.source_backend_env, {
    ENVIRONMENT           = "production"
    FRONTEND_APP_URL      = local.frontend_url
    FRONTEND_ORIGINS      = local.frontend_url
    ORACLE_REDIRECT_URI   = "${local.backend_url}/auth/oracle/callback"
    ORACLE_LAUNCH_URI     = "${local.backend_url}/auth/oracle/launch"
    EPIC_REDIRECT_URI     = "${local.backend_url}/auth/epic/callback"
    EPIC_LAUNCH_URI       = "${local.backend_url}/auth/epic/launch"
    CARDINAL_LLM_PROVIDER = "gemma4"
    SLM_BASE_URL          = local.model_url
    SLM_AUTH_AUDIENCE     = local.model_url
    SLM_CHAT_PATH         = "/v1/chat/completions"
    SLM_MODEL             = "gemma4-26b-a4b-it"
    SLM_AUTH_MODE         = "gcp_identity"
    SLM_API_KEY           = ""
    SLM_TIMEOUT_SECONDS   = "600"
  })

  backend_secret_env = jsondecode(file("${path.module}/backend_secret_env.production.json"))
}
