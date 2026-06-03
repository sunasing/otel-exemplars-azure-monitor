# otel-exemplars-azure-monitor

Minimal Go sample that emits OpenTelemetry **logs**, **traces**, and **metrics with exemplars** and sends everything to **Azure Monitor through OpenTelemetry Collector**.

## Prerequisites

- Go 1.22+
- Docker (for running the collector)
- Azure Monitor / Application Insights connection string in `APPLICATIONINSIGHTS_CONNECTION_STRING`

## Run the collector

```bash
docker run --rm -it \
  -p 4317:4317 -p 4318:4318 \
  -e APPLICATIONINSIGHTS_CONNECTION_STRING="$APPLICATIONINSIGHTS_CONNECTION_STRING" \
  -v "$(pwd)/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml" \
  otel/opentelemetry-collector-contrib:latest
```

The collector receives OTLP telemetry and exports logs/traces/metrics to Azure Monitor using the `azuremonitor` exporter.

## Run the app

```bash
go run .
```

Optional environment variable:

- `OTEL_EXPORTER_OTLP_ENDPOINT` (default: `localhost:4317`)

## What this app emits

- **Trace**: span `process-request` every 2 seconds
- **Metric**: histogram `http.server.request.duration` every 2 seconds
- **Exemplar**: histogram points are recorded with active span context so exemplars can include trace/span IDs
- **Log**: structured log `processed synthetic request` attached to the same context
