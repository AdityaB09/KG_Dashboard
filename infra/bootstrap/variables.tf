variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "github_owner" {
  type    = string
  default = "AdityaB09"
}

variable "github_repository" {
  type    = string
  default = "KG_Dashboard"
}

variable "github_deploy_branch" {
  type    = string
  default = "master"
}

variable "artifact_repository" {
  type    = string
  default = "cardinal-app"
}

variable "state_bucket_name" {
  type = string
}

variable "model_bucket_name" {
  type = string
}

variable "github_pool_id" {
  type    = string
  default = "github-cardinal-pool"
}

variable "github_provider_id" {
  type    = string
  default = "github-cardinal-provider"
}

variable "billing_account_id" {
  type    = string
  default = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 50
}

variable "bootstrap_operator_email" {
  type    = string
  default = ""
}
