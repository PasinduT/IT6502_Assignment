output "frontend_url" {
  value = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "backend_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "storage_account_name" {
  value = azurerm_storage_account.main.name
}

output "search_service_name" {
  value = azurerm_search_service.main.name
}

output "static_web_app_api_key" {
  value     = azurerm_static_web_app.frontend.api_key
  sensitive = true
}

