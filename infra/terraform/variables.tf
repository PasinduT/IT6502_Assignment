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
  # Azure hybrid/RRF scores are small (typically around 0.01–0.03), unlike
  # normalized similarity scores. Tune this after evaluating the deployed corpus.
  default = 0.01
}

variable "guide_media_container_name" {
  description = "Blob container for intentionally public, approved guide images."
  type        = string
  default     = "guide-images"

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$", var.guide_media_container_name))
    error_message = "guide_media_container_name must be a valid Azure Blob container name."
  }
}
