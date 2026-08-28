variable "azure_subscription_id" {
  description = "Azure subscription used for deployment."
  type        = string
}

variable "project_name" {
  description = "Short lowercase project identifier used in resource names."
  type        = string
  default     = "lktaxassistant"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region for regional resources."
  type        = string
  default     = "southeastasia"
}

variable "static_web_app_location" {
  description = "Azure region for the Static Web App (Southeast Asia is unsupported)."
  type        = string
  default     = "eastasia"
}

variable "container_image" {
  description = "Public GHCR image for the FastAPI backend."
  type        = string
}

variable "gemini_api_key" {
  description = "Gemini API key passed to the Container App as a secret."
  type        = string
  sensitive   = true
}

variable "gemini_model" {
  type    = string
  default = "gemini-3.5-flash-lite"
}

variable "gemini_embedding_model" {
  type    = string
  default = "gemini-embedding-2"
}

variable "embedding_dimensions" {
  type    = number
  default = 768
}

variable "rag_min_score" {
  type    = number
  default = 0.25
}
