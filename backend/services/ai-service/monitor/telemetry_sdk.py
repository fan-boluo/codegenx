from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader


class TelemetrySDK:
    """OpenTelemetry SDK 初始化与配置"""

    @staticmethod
    def init(
        service_name: str,
        service_version: str,
        otlp_endpoint: str,            # OTLP Collector 地址
        resource_attributes: dict = None
    ) -> None:
        """
        应用启动时调用一次。
        设置全局 TracerProvider 和 MeterProvider。
        """
        resource = Resource.create({
            "service.name": service_name,
            "service.version": service_version,
            **(resource_attributes or {})
        })

        # Trace
        tracer_provider = TracerProvider(resource=resource)
        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)

        # Metrics
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
            export_interval_millis=15000
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)

    @staticmethod
    def shutdown() -> None:
        """应用关闭时调用，flush 剩余数据"""
        trace.get_tracer_provider().shutdown()
        metrics.get_meter_provider().shutdown()