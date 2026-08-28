# Azure and GitHub setup

This guide starts from an Azure subscription and GitHub repository with no
project-specific Azure resources or deployment identity. Complete it once
before allowing the deployment workflow to run.

The commands use placeholders and shell variables. They do not rely on an
existing service principal or on a particular subscription ID.

## 1. Install the required tools

Install:

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- [Terraform](https://developer.hashicorp.com/terraform/install)
- [Git](https://git-scm.com/downloads)

You also need:

- an Azure subscription;
- Owner access, or equivalent resource and role-assignment permissions, in the
  Azure subscription;
- permission in Microsoft Entra ID to create an app registration; and
- administrator access to the GitHub repository settings.

Check the command-line tools:

```bash
az version
terraform version
git --version
```

## 2. Choose names for this deployment

Run the following in the terminal. Replace the three values in angle brackets.
The storage account name must be globally unique, 3–24 characters long, and
contain only lowercase letters and numbers.

```bash
GITHUB_OWNER="<your-github-user-or-organization>"
GITHUB_REPOSITORY="<your-repository-name>"
STATE_ACCOUNT="<globally-unique-storage-account-name>"

AZURE_LOCATION="southeastasia"
APP_RESOURCE_GROUP="rg-lktaxassistant-dev"
STATE_RESOURCE_GROUP="rg-lktaxassistant-tfstate"
STATE_CONTAINER="tfstate"
DEPLOYER_NAME="github-${GITHUB_REPOSITORY}-deployer"
```

These variables exist only in the current terminal. If you open a new terminal,
set them again before continuing.

## 3. Sign in and select the Azure subscription

```bash
az login
az account list --output table
az account set --subscription "<subscription-name-or-id>"

AZURE_SUBSCRIPTION_ID=$(az account show --query id --output tsv)
AZURE_TENANT_ID=$(az account show --query tenantId --output tsv)
az account show --query '{name:name,id:id,tenantId:tenantId}' --output table
```

The final command lets you confirm the selected subscription before creating
anything.

## 4. Register the Azure resource providers

Registration can take several minutes. Re-running these commands is safe.

```bash
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.Search --wait
az provider register --namespace Microsoft.Storage --wait
az provider register --namespace Microsoft.Web --wait
```

## 5. Create the Terraform state storage

Terraform cannot create the storage that holds its own state because it needs
that storage before initialization. This is the only infrastructure created
outside Terraform.

The state has its own resource group so it is not accidentally deleted with the
application. The application resource group is created now so the deployment
identity can receive access only to that group; the workflow imports it into
Terraform state on the first run.

```bash
az group create \
  --name "$STATE_RESOURCE_GROUP" \
  --location "$AZURE_LOCATION"

az group create \
  --name "$APP_RESOURCE_GROUP" \
  --location "$AZURE_LOCATION"

az storage account create \
  --name "$STATE_ACCOUNT" \
  --resource-group "$STATE_RESOURCE_GROUP" \
  --location "$AZURE_LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --access-tier Hot \
  --https-only true \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false

az storage container create \
  --name "$STATE_CONTAINER" \
  --account-name "$STATE_ACCOUNT" \
  --auth-mode key \
  --public-access off
```

Do not add `--fail-on-exist false` to the last command. `--fail-on-exist` is a
switch and does not accept `false`; that was the cause of the earlier bootstrap
failure. If the groups or account already exist, set the variables to their
names and continue with the missing command.

## 6. Create the GitHub deployment identity

An Azure app registration and its service principal represent GitHub Actions.
No client secret is created: GitHub authenticates with OpenID Connect (OIDC).

```bash
AZURE_CLIENT_ID=$(az ad app create \
  --display-name "$DEPLOYER_NAME" \
  --query appId \
  --output tsv)

SERVICE_PRINCIPAL_OBJECT_ID=$(az ad sp create \
  --id "$AZURE_CLIENT_ID" \
  --query id \
  --output tsv)

printf 'Client ID: %s\nService principal object ID: %s\n' \
  "$AZURE_CLIENT_ID" "$SERVICE_PRINCIPAL_OBJECT_ID"
```

If Azure reports that the application is not yet available, wait a minute and
repeat only the `az ad sp create` command. The client ID and service-principal
object ID are different values.

## 7. Trust the GitHub Actions workflow with OIDC

The Azure Portal is the simplest method because GitHub's OIDC subject format
depends on when the GitHub repository was created.

1. Open **Microsoft Entra ID → App registrations** in the Azure Portal.
2. Select the application named by `DEPLOYER_NAME`.
3. Open **Certificates & secrets → Federated credentials**.
4. Select **Add credential → GitHub Actions deploying Azure resources**.
5. Enter the GitHub owner and repository from step 2.
6. Choose **Entity type: Branch** and **GitHub branch name: main**.
7. Give the credential a name such as `github-main` and save it.

Do not create a client secret. The workflow requests a short-lived OIDC token
for the `main` branch instead.

## 8. Grant only the required Azure roles

The deployment identity needs Contributor on the application resource group and
Storage Blob Data Contributor on the state account. It does not need access to
the entire subscription.

```bash
APP_RESOURCE_GROUP_ID=$(az group show \
  --name "$APP_RESOURCE_GROUP" \
  --query id \
  --output tsv)

STATE_ACCOUNT_ID=$(az storage account show \
  --name "$STATE_ACCOUNT" \
  --resource-group "$STATE_RESOURCE_GROUP" \
  --query id \
  --output tsv)

az role assignment create \
  --assignee-object-id "$SERVICE_PRINCIPAL_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "$APP_RESOURCE_GROUP_ID"

az role assignment create \
  --assignee-object-id "$SERVICE_PRINCIPAL_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$STATE_ACCOUNT_ID"
```

Role assignments can take several minutes to become usable.

## 9. Add the GitHub secrets and variable

In the GitHub repository, open **Settings → Secrets and variables → Actions**.

Create these repository secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | The value printed in step 6 |
| `AZURE_TENANT_ID` | The value captured in step 3 |
| `AZURE_SUBSCRIPTION_ID` | The value captured in step 3 |
| `GEMINI_API_KEY` | Your Gemini API key |

`GEMINI_API_KEY` is a GitHub Actions secret. Do not put it in a Terraform
variables file or commit it to Git.

On the **Variables** tab, create this repository variable:

| Variable | Value |
|---|---|
| `TF_STATE_STORAGE_ACCOUNT` | The `STATE_ACCOUNT` name chosen in step 2 |

The resource-group, container, and state-file names are already defined in the
workflow. Only the storage account is a variable because its name must be
globally unique.

## 10. Verify the one-time setup

```bash
az storage container show \
  --name "$STATE_CONTAINER" \
  --account-name "$STATE_ACCOUNT" \
  --auth-mode key \
  --query '{name:name,publicAccess:properties.publicAccess}'

az role assignment list \
  --assignee-object-id "$SERVICE_PRINCIPAL_OBJECT_ID" \
  --all \
  --query '[].{role:roleDefinitionName,scope:scope}' \
  --output table

az ad app federated-credential list \
  --id "$AZURE_CLIENT_ID" \
  --query '[].{name:name,subject:subject}' \
  --output table
```

You should see the `tfstate` container, the two scoped roles, and one federated
credential for the `main` branch.

## 11. Optional local Terraform check

From the repository root:

```bash
cd infra/terraform

STATE_ACCESS_KEY=$(az storage account keys list \
  --resource-group "$STATE_RESOURCE_GROUP" \
  --account-name "$STATE_ACCOUNT" \
  --query '[0].value' \
  --output tsv)

ARM_ACCESS_KEY="$STATE_ACCESS_KEY" terraform init -reconfigure \
  -backend-config="resource_group_name=$STATE_RESOURCE_GROUP" \
  -backend-config="storage_account_name=$STATE_ACCOUNT" \
  -backend-config="container_name=$STATE_CONTAINER" \
  -backend-config="key=lktaxassistant-dev.tfstate"

unset STATE_ACCESS_KEY

terraform fmt -check -recursive
terraform validate
```

The access key is held temporarily in the current process and is not written to
the command output. This avoids granting your personal Azure identity an extra
blob-data role solely for a local check. GitHub Actions does not use this key;
it uses OIDC and the service principal's scoped Storage Blob Data Contributor
role.

Do not run a local `terraform apply` unless you also supply a published backend
image and the protected Gemini key. The normal end-to-end deployment is the
GitHub Actions workflow.

## 12. Deploy

Commit the repository changes and push them to the `main` branch when you are
ready. The **Deploy** workflow will:

1. build and publish the backend image;
2. authenticate to Azure using OIDC;
3. initialize the remote Terraform state;
4. import the pre-created application resource group on the first run;
5. create or update the Azure infrastructure and Search index; and
6. build and deploy the frontend.

The first image push creates a GHCR package named after the repository with
`-api` appended. New GHCR packages are private by default, and GitHub does not
provide a supported workflow API for changing package visibility. On the first
run, if **Verify the GHCR image is public** stops the workflow:

1. Open your GitHub profile or organization and select **Packages**.
2. Open the package ending in `-api`.
3. Select **Package settings**.
4. Under **Danger Zone**, select **Change visibility → Public**.
5. Enter the package name to confirm the permanent visibility change.
6. Return to the failed workflow and select **Re-run all jobs**.

The workflow deliberately signs out of GHCR and performs an anonymous manifest
lookup before Terraform runs. If that check passes, Azure Container Apps can
pull the image without registry credentials. A public GHCR package can be read
anonymously; do not publish secrets or private application content in the
image.

Open **GitHub → Actions → Deploy** to follow the run. Terraform manages the
application infrastructure after this one-time setup; no bootstrap shell script
is required.
