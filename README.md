# Demo app with OpenTelemetry logs, traces, metrics with exemplars -> Azure Monitor

This repository has a demo app that sends OpenTelemetry logs, traces, metrics with exemplars to Azure Monitor through OTel Collector. You can view the exemplars in Azure Managed Grafana and open the corresponding distributed trace in Azure Monitor.

```
┌─────────────────────────┐ OTLP/gRPC  ┌──────────────────────────┐      ┌───────────────────────────────┐      ┌──────────────────────────┐ 
│ demo-app (Python/Flask) │ ─────────► │ OTel Collector (contrib) │ ───► │  Azure Monitor                │ ───► │       Visualize          │
│ namespace: demo         │            │ namespace: observability │      │  Logs and Traces in LAW       |      │   Azure Managed Grafana  │
│                         |            |                          |      |  Metrics with Exemplars in AMW|

```

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Azure CLI | 2.55 |
| kubectl | 1.28 |
| Docker / Buildx | 24 |
| AKS or any Kubernetes cluster | 1.28 |
| Azure Container Registry (ACR) or dockerhub | any |

---

## Step 1 - Download this repository

- main.py, Dockerfile, requirements.txt: These files will dockerize the demo app
- demo-app.yaml: to deploy the app in your kubernetes/AKS cluster. We will update this file later in the following steps.
- otel-collector.yaml: to deploy otel collector. We will update this file later in the following steps.

## Step 2 — Configure Azure resources (LAW, AMW, App Insights) and configure Otel collector

For this demo tutorial, you will need to manually create and configure Data Collection Endpoints (DCE), Data Collection Rules (DCR), and destination workspaces (LAW, AMW, AppInsights). Follow instructions in the below article to configure the same and also configure the Otel collector. The otel collector config needs to be updated in the **otel-collector.yaml** file as part of this repository.

[Configure AMW, LAW and Otel collector](https://learn.microsoft.com/en-us/azure/azure-monitor/containers/opentelemetry-protocol-ingestion)

Update the below section in the otel-collector.yaml:

```yaml
exporters:
  otlp_http/azuremonitor:
    metrics_endpoint: "https://<metrics-ingestion-endpoint>/datacollectionRules/<dcr-immutableid>/streams/Custom-Metrics-Otel/otlp/v1/metrics"
    traces_endpoint: "https://<logs-ingestion-endpoint>/datacollectionRules/<dcr-immutableid>/streams/Microsoft-OTLP-Traces/otlp/v1/traces"
    logs_endpoint: "https://<logs-ingestion-endpoint>/datacollectionRules/<dcr-immutableid>/streams/Microsoft-OTLP-Logs/otlp/v1/logs"
    auth:
      authenticator: azure_auth/monitor
```

## Step 2 — Build and push the demo app image

```bash
# Log in to ACR from cli
az login
az account set --subscription <subscriptionid>
az acr login --name <YOUR_ACR>

# Build + push
docker buildx build --platform linux/amd64 -t <YOUR_ACR>.azurecr.io/otel-demo-app:latest . --push
```
You might have to log in to the ACR

## Step 3 — Attach ACR to AKS (if not done already)

```bash
az aks update \
  --name <AKS_CLUSTER> \
  --resource-group <RESOURCE_GROUP> \
  --attach-acr <YOUR_ACR>
```

## Step 4 — Update demo-app.yaml

Edit `k8s/demo-app.yaml` to use the image that you just created

```yaml
image: <YOUR_ACR>.azurecr.io/otel-demo-app:latest
```
Also update `K8S_CLUSTER_NAME` in both the YAML files to match your AKS cluster name.

## Step 5 — Deploy app

```bash
# Connect kubectl to your cluster
az aks get-credentials --name <AKS_CLUSTER> --resource-group <RESOURCE_GROUP>

# Deploy collector (creates observability namespace, RBAC, etc.)
kubectl apply -f otel-collector.yaml

# Deploy demo app
kubectl apply -f demo-app.yaml

# Verify
kubectl get pods -n observability
kubectl get pods -n demo
```

## Step 6 — Send test traffic

```bash
# Port-forward the demo app
kubectl port-forward -n demo svc/demo-app 8080:80

# In another terminal run the below command to generate test traffic
curl -s -X POST "http://localhost:8080/order" -H "Content-Type: application/json" -d '{"item":"widget"}'
```

In a browser, go to http://localhost:8080/error, http://localhost:8080/health to generate more traffic.


## Visualize

Following metrics are sent by the app:

| Metric name | Type | Description |
|-------------|------|-------------|
| `http.server.request_count` | Counter | Requests by method/status/route |
| `http.server.duration` | Histogram | Latency in ms (with exemplars) |
| `http.server.active_requests` | UpDownCounter | In-flight requests |
| `business.order.value` | Histogram | Order USD value (with exemplars) |

**Exemplars** link histogram buckets directly to sampled traces — click a spike on the latency chart and jump straight to the offending trace.

### Configure Azure Managed Grafana to use exemplars
1.	Navigate to Connections -> Data Sources in Azure Managed Grafana. Since you have connected The Azure Managed Grafana to Azure Monitor Workspace, you will see the data source (Managed_Prometheus_<AMW-Name>) already configured. If the data source is not configured, follow the steps here to add your Azure Monitor Workspace as a data source.
2.	Open the data source configuration.
3.	Click Add Exemplars to enable exemplar support.

1.	In the exemplar configuration section, toggle Internal Link to On.
2.	Select Azure Monitor as the data source.
3.	In the Label Name, enter the name of the field in the labels object that should be used to get the trace id, eg. trace_id.
4.	Click Save & Test.

1.	Navigate to a Grafana dashboard that uses your configured Prometheus data source.
2.	Open the panel options for a metrics chart.
3.	Toggle Exemplars to On.

<img width="628" height="421" alt="image" src="https://github.com/user-attachments/assets/7aa0309f-f6fc-4014-bd0d-e76e2de17415" />

### Logs
Structured log records with `trace_id` and `span_id` correlation — find the log in Application Insights **Traces** table and click directly into the parent trace.

---

### Useful collector extensions

| Extension | URL |
|-----------|-----|
| zPages (live pipeline stats) | `kubectl port-forward -n observability svc/otel-collector 55679:55679` then open `http://localhost:55679/debug/tracez` |
| Health check | `http://localhost:13133/` |

---
