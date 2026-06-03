"""
OpenTelemetry demo app — emits logs, metrics, traces, and exemplars.
Exports via OTLP gRPC to the OTel Collector sidecar / DaemonSet.
"""

import os
import time
import random
import logging

from flask import Flask, jsonify, request

# ── OpenTelemetry SDK ────────────────────────────────────────────────────────
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics._internal.aggregation import ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics._internal.exemplar import TraceBasedExemplarFilter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

# ── Configuration ─────────────────────────────────────────────────────────────
OTLP_ENDPOINT   = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE         = os.getenv("OTEL_SERVICE_NAME", "demo-service")
SERVICE_VER     = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
ENVIRONMENT     = os.getenv("DEPLOYMENT_ENVIRONMENT", "production")

resource = Resource.create({
    SERVICE_NAME:    SERVICE,
    SERVICE_VERSION: SERVICE_VER,
    "deployment.environment": ENVIRONMENT,
    "k8s.cluster.name": os.getenv("K8S_CLUSTER_NAME", "aks-demo"),
    "k8s.namespace.name": os.getenv("K8S_NAMESPACE", "default"),
    "k8s.pod.name":  os.getenv("K8S_POD_NAME", "unknown"),
    "k8s.node.name": os.getenv("K8S_NODE_NAME", "unknown"),
})

# ── Tracer Provider ───────────────────────────────────────────────────────────
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer(__name__)

# ── Meter Provider (with exemplars enabled) ───────────────────────────────────
# TraceBasedExemplarFilter automatically links histogram data points to the
# active span's trace ID and span ID — no manual wiring is needed. Every
# histogram.record() call made while a span is active will carry an exemplar.
metric_exporter = OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True)
metric_reader   = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15_000)
meter_provider  = MeterProvider(
    resource=resource,
    metric_readers=[metric_reader],
    exemplar_filter=TraceBasedExemplarFilter(),
    views=[
        # Buckets covering order values $10-$500
        View(
            instrument_name="business.order.value",
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=[10, 25, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600]
            ),
        ),
        # Buckets covering request latency 0-2000ms
        View(
            instrument_name="http.server.duration",
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=[5, 10, 25, 50, 75, 100, 150, 200, 300, 400, 500, 750, 1000, 2000]
            ),
        ),
    ],
)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__, version=SERVICE_VER)

# ── Logger Provider ───────────────────────────────────────────────────────────
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
logging.basicConfig(level=logging.INFO)
otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
logging.getLogger().addHandler(otel_handler)
logger = logging.getLogger("demo-service")

# ── Instruments ───────────────────────────────────────────────────────────────
request_counter = meter.create_counter(
    name="http.server.request_count",
    description="Total HTTP requests received",
    unit="1",
)
request_duration = meter.create_histogram(
    name="http.server.duration",
    description="HTTP request duration",
    unit="ms",
)
active_requests = meter.create_up_down_counter(
    name="http.server.active_requests",
    description="Currently in-flight HTTP requests",
    unit="1",
)
order_value = meter.create_histogram(
    name="business.order.value",
    description="Value of processed orders",
    unit="USD",
)

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()


@app.route("/health")
def health():
    return jsonify(status="ok"), 200


@app.route("/")
def index():
    # FIX: start the timer and record all metrics inside the span so that
    # TraceBasedExemplarFilter can link each data point to the active span.
    with tracer.start_as_current_span("handle-root") as span:
        start = time.time()  # FIX: moved inside the span
        active_requests.add(1, {"http.method": "GET", "http.route": "/"})

        span.set_attribute("http.method", "GET")
        span.set_attribute("http.route", "/")
        logger.info("Root endpoint called", extra={"http.route": "/"})

        duration_ms = (time.time() - start) * 1000
        ctx = span.get_span_context()

        request_counter.add(1, {
            "http.method": "GET",
            "http.status_code": "200",
            "http.route": "/",
        })
        # FIX: recording happens while the span is still active — exemplar
        # is attached automatically by TraceBasedExemplarFilter.
        request_duration.record(duration_ms, {
            "http.method": "GET",
            "http.status_code": "200",
            "http.route": "/",
        })

        # FIX: decrement also inside the span so the context is still active.
        active_requests.add(-1, {"http.method": "GET", "http.route": "/"})

    return jsonify(message="Hello from OTel demo!", trace_id=format(ctx.trace_id, "032x"))


@app.route("/order", methods=["POST"])
def create_order():
    with tracer.start_as_current_span("create-order") as span:
        start = time.time()  # FIX: moved inside the span
        active_requests.add(1, {"http.method": "POST", "http.route": "/order"})

        value = random.uniform(10, 500)
        span.set_attribute("order.value", value)
        span.set_attribute("order.currency", "USD")

        # Simulate downstream DB call
        with tracer.start_as_current_span("db-insert-order") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "INSERT")
            time.sleep(random.uniform(0.01, 0.05))

        logger.info("Order created", extra={"order.value": value})
        order_value.record(value, {"order.currency": "USD", "order.status": "created"})

        ctx = span.get_span_context()
        duration_ms = (time.time() - start) * 1000
        request_counter.add(1, {"http.method": "POST", "http.status_code": "201", "http.route": "/order"})
        request_duration.record(duration_ms, {"http.method": "POST", "http.status_code": "201", "http.route": "/order"})

        # FIX: decrement inside the span.
        active_requests.add(-1, {"http.method": "POST", "http.route": "/order"})

    return jsonify(order_id=random.randint(1000, 9999), value=round(value, 2)), 201


@app.route("/error")
def trigger_error():
    with tracer.start_as_current_span("trigger-error") as span:
        start = time.time()
        try:
            raise ValueError("Simulated application error")
        except ValueError as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            logger.error("Simulated error occurred", exc_info=True)

            duration_ms = (time.time() - start) * 1000
            request_counter.add(1, {"http.method": "GET", "http.status_code": "500", "http.route": "/error"})
            # FIX: record duration so the histogram (and its exemplar) is
            # populated for error responses too.
            request_duration.record(duration_ms, {
                "http.method": "GET",
                "http.status_code": "500",
                "http.route": "/error",
            })
            return jsonify(error="Simulated error"), 500


if __name__ == "__main__":
    logger.info("Starting demo service", extra={"service": SERVICE, "version": SERVICE_VER})
    app.run(host="0.0.0.0", port=8080)
