resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  secret {
    name  = "gemini-api-key"
    value = var.gemini_api_key
  }
  secret {
    name  = "search-key"
    value = azurerm_search_service.main.primary_key
  }

  template {
    min_replicas = 0
    max_replicas = 2
    container {
      name   = "api"
      image  = var.container_image
      cpu    = 0.25
      memory = "0.5Gi"

      env { name = "ENVIRONMENT" value = "production" }
      env { name = "GEMINI_API_KEY" secret_name = "gemini-api-key" }
      env { name = "GEMINI_MODEL" value = var.gemini_model }
      env { name = "GEMINI_EMBEDDING_MODEL" value = var.gemini_embedding_model }
      env { name = "EMBEDDING_DIMENSIONS" value = tostring(var.embedding_dimensions) }
      env { name = "AZURE_SEARCH_ENDPOINT" value = "https://${azurerm_search_service.main.name}.search.windows.net" }
      env { name = "AZURE_SEARCH_INDEX" value = "tax-assistant" }
      env { name = "AZURE_SEARCH_KEY" secret_name = "search-key" }
      env { name = "AZURE_STORAGE_ACCOUNT_URL" value = azurerm_storage_account.main.primary_blob_endpoint }
      env { name = "AZURE_STORAGE_CONTAINER" value = azurerm_storage_container.documents.name }
      env { name = "FRONTEND_ORIGIN" value = "https://${azurerm_static_web_app.frontend.default_host_name}" }
      env { name = "RAG_TOP_K" value = "6" }
      env { name = "RAG_MIN_SCORE" value = tostring(var.rag_min_score) }
      env { name = "MAX_MESSAGE_CHARS" value = "8000" }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

