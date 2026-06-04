# Demo app with OpenTelemetry logs, traces, metrics with exemplars -> Azure Monitor

This repository has a demo app that sends OpenTelemetry logs, traces, metrics with exemplars to Azure Monitor through OTel Collector. You can view the exemplars in Azure Managed Grafana and open the corresponding distributed trace in Azure Monitor.

```
┌──────────────────────────┐   OTLP/gRPC   ┌──────────────────────────┐               ┌─────────────────────────────────┐               ┌──────────────────────────┐ 
│  demo-app (Python/Flask) │ ────────────► │  OTel Collector (contrib) │ ────────────► │  Azure Monitor                 │ ────────────► │            Visualize     │
│  namespace: demo         │               │  namespace: observability  │              │  Logs and Traces in LAW        |               │   Azure Managed Grafana  │
│                          |               |                            |              |  Metrics with Exemplars in AMW |

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
    metrics_endpoint: "https://otlpdce-ex-bb9p.westus2-1.metrics.ingest.monitor.azure.com/datacollectionRules/dcr-510db2c7534a458a8e8e64a79155b2df/streams/Custom-Metrics-Otel/otlp/v1/metrics"
    traces_endpoint: "https://otlpdce-ex-bb9p.westus2-1.ingest.monitor.azure.com/datacollectionRules/dcr-510db2c7534a458a8e8e64a79155b2df/streams/Microsoft-OTLP-Traces/otlp/v1/traces"
    logs_endpoint: "https://otlpdce-ex-bb9p.westus2-1.ingest.monitor.azure.com/datacollectionRules/dcr-510db2c7534a458a8e8e64a79155b2df/streams/Microsoft-OTLP-Logs/otlp/v1/logs"
    auth:
      authenticator: azure_auth/monitor
```

## Step 2 — Build and push the demo app image

```bash
# Log in to ACR
az acr login --name <YOUR_ACR>

# Build + push
docker buildx build \
  --platform linux/amd64 \
  -t <YOUR_ACR>.azurecr.io/otel-demo-app:latest \
  ./app --push
```


## Step 3 — Configure secrets and image references


> **Tip:** For production, use Azure Key Vault + the Secrets Store CSI Driver instead of a plain Secret.

### 3b. Demo app — point to your ACR image

Edit `k8s/demo-app.yaml`:

```yaml
image: <YOUR_ACR>.azurecr.io/otel-demo-app:latest
```

Also update `K8S_CLUSTER_NAME` in both YAML files to match your AKS cluster name.

---

## Step 4 — Attach ACR to AKS (if not done already)

```bash
az aks update \
  --name <AKS_CLUSTER> \
  --resource-group <RESOURCE_GROUP> \
  --attach-acr <YOUR_ACR>
```

---

## Step 5 — Deploy

```bash
# Connect kubectl to your cluster
az aks get-credentials --name <AKS_CLUSTER> --resource-group <RESOURCE_GROUP>

# Deploy collector (creates observability namespace, RBAC, etc.)
kubectl apply -f k8s/otel-collector.yaml

# Deploy demo app
kubectl apply -f k8s/demo-app.yaml

# Verify
kubectl get pods -n observability
kubectl get pods -n demo
```

---

## Step 6 — Send test traffic

```bash
# Port-forward the demo app
kubectl port-forward -n demo svc/demo-app 8080:80

# In another terminal
./load-gen.sh http://localhost:8080
```

---

## What you'll see in Azure Monitor

### Application Map
Distributed traces showing `demo-service → db-insert-order` spans.

### Transaction Search
Individual traces with full span attributes, status codes, and exception details.

### Metrics Explorer
Custom metrics available immediately after data arrives:

| Metric name | Type | Description |
|-------------|------|-------------|
| `http.server.request_count` | Counter | Requests by method/status/route |
| `http.server.duration` | Histogram | Latency in ms (with exemplars) |
| `http.server.active_requests` | UpDownCounter | In-flight requests |
| `business.order.value` | Histogram | Order USD value (with exemplars) |

**Exemplars** link histogram buckets directly to sampled traces — click a spike on the latency chart and jump straight to the offending trace.

### Logs
Structured log records with `trace_id` and `span_id` correlation — find the log in Application Insights **Traces** table and click directly into the parent trace.

---

## Exemplar deep-dive

Exemplars are enabled via two mechanisms:

1. **SDK side** — the env var `OTEL_METRICS_EXEMPLAR_FILTER=trace_based` tells the OTel Python SDK to attach the current `trace_id` + `span_id` to histogram data points whenever a span is active during `record()` / `add()`.

2. **Collector side** — the `azuremonitor` exporter preserves exemplar fields in the OTLP payload when forwarding histograms to Application Insights.

No extra code is needed; exemplars are emitted automatically on any `histogram.record()` call made inside a span context.

---

## Customising the collector

The `otel-collector-config.yaml` (standalone) and the inline ConfigMap in `otel-collector.yaml` are kept in sync. Edit the ConfigMap and run:

```bash
kubectl apply -f k8s/otel-collector.yaml
kubectl rollout restart deployment/otel-collector -n observability
```

### Useful collector extensions

| Extension | URL |
|-----------|-----|
| zPages (live pipeline stats) | `kubectl port-forward -n observability svc/otel-collector 55679:55679` then open `http://localhost:55679/debug/tracez` |
| Health check | `http://localhost:13133/` |

---

## Production hardening checklist

- [ ] Replace the plain Secret with Azure Key Vault + CSI driver
- [ ] Set `debug` exporter `verbosity: basic` or remove it entirely
- [ ] Tune `batch` processor sizes for your throughput
- [ ] Add `HorizontalPodAutoscaler` to the collector Deployment
- [ ] Enable TLS on the OTLP receiver if collector is exposed externally
- [ ] Add network policies to restrict OTLP traffic to your namespaces
- [ ] Use Workload Identity for the collector's Service Account (avoids storing secrets at all)
