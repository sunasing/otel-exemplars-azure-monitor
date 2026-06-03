package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	"go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"

	logglobal "go.opentelemetry.io/otel/log/global"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	shutdown, err := setupOTel(ctx)
	if err != nil {
		panic(err)
	}
	defer func() {
		if err := shutdown(context.Background()); err != nil {
			fmt.Fprintf(os.Stderr, "shutdown error: %v\n", err)
		}
	}()

	logger := slog.New(otelslog.NewHandler("otel-exemplars-app"))
	meter := otel.Meter("otel-exemplars-app")
	tracer := otel.Tracer("otel-exemplars-app")

	requestLatency, err := meter.Float64Histogram(
		"http.server.request.duration",
		metric.WithUnit("ms"),
		metric.WithDescription("Synthetic request latency"),
	)
	if err != nil {
		panic(err)
	}

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			func() {
				spanCtx, span := tracer.Start(ctx, "process-request")
				defer span.End()

				latencyMs := 50 + rand.Float64()*250
				requestLatency.Record(
					spanCtx,
					latencyMs,
					metric.WithAttributes(attribute.String("route", "/checkout"), attribute.String("method", "GET")),
				)
				span.SetAttributes(attribute.Float64("request.latency.ms", latencyMs))

				logger.InfoContext(
					spanCtx,
					"processed synthetic request",
					"latency_ms",
					latencyMs,
					"route",
					"/checkout",
				)
			}()
		}
	}
}

func setupOTel(ctx context.Context) (func(context.Context) error, error) {
	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "localhost:4317"
	}

	res, err := resource.New(ctx,
		resource.WithSchemaURL(semconv.SchemaURL),
		resource.WithAttributes(
			semconv.ServiceName("otel-exemplars-azure-monitor"),
			semconv.ServiceVersion("1.0.0"),
		),
	)
	if err != nil {
		return nil, err
	}

	traceExp, err := otlptracegrpc.New(ctx,
		otlptracegrpc.WithEndpoint(endpoint),
		otlptracegrpc.WithInsecure(),
	)
	if err != nil {
		return nil, err
	}

	metricExp, err := otlpmetricgrpc.New(ctx,
		otlpmetricgrpc.WithEndpoint(endpoint),
		otlpmetricgrpc.WithInsecure(),
	)
	if err != nil {
		return nil, err
	}

	logExp, err := otlploggrpc.New(ctx,
		otlploggrpc.WithEndpoint(endpoint),
		otlploggrpc.WithInsecure(),
	)
	if err != nil {
		return nil, err
	}

	tp := trace.NewTracerProvider(
		trace.WithSampler(trace.AlwaysSample()),
		trace.WithBatcher(traceExp),
		trace.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})

	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(metricExp, sdkmetric.WithInterval(5*time.Second))),
		sdkmetric.WithResource(res),
	)
	otel.SetMeterProvider(mp)

	lp := sdklog.NewLoggerProvider(
		sdklog.WithProcessor(sdklog.NewBatchProcessor(logExp)),
		sdklog.WithResource(res),
	)
	logglobal.SetLoggerProvider(lp)

	return func(ctx context.Context) error {
		var result error
		if err := lp.Shutdown(ctx); err != nil {
			result = errors.Join(result, err)
		}
		if err := mp.Shutdown(ctx); err != nil {
			result = errors.Join(result, err)
		}
		if err := tp.Shutdown(ctx); err != nil {
			result = errors.Join(result, err)
		}
		return result
	}, nil
}
