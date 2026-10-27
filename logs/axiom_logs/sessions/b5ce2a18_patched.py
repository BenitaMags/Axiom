# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from concurrent.futures import Future
from confluent_kafka import Producer
from locust import Locust
from locust.wait_time import constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with bootstrap.servers config and wrap it
    to accept a value_serializer callable that is applied to values in send().

    kafka-python's KafkaProducer accepts `bootstrap_servers` and `value_serializer`
    as constructor arguments. confluent-kafka's Producer uses `bootstrap.servers`
    (with a dot) and has no built-in value_serializer — serialization must be
    applied manually before calling produce().

    This wrapper creates a Producer with the correct config key and returns a
    wrapper object that stores the value_serializer for later use by
    _confluent_send_wrapper.
    """
    config = {"bootstrap.servers": bootstrap_servers}
    producer = Producer(config)
    # Attach the serializer to the producer instance for use in send()
    producer._value_serializer = value_serializer
    return producer


class _ConfluentFuture:
    """
    Future-like object compatible with kafka-python's Future interface
    (add_callback / add_errback).

    confluent-kafka uses a delivery_report callback (err, msg) instead of
    futures. This class bridges the gap by storing success/error callbacks
    and invoking them from the delivery_report.
    """

    def __init__(self):
        self._callbacks = []
        self._errbacks = []

    def add_callback(self, fn, *args, **kwargs):
        """Store a success callback to be called when delivery succeeds."""
        self._callbacks.append((fn, args, kwargs))

    def add_errback(self, fn, *args, **kwargs):
        """Store an error callback to be called when delivery fails."""
        self._errbacks.append((fn, args, kwargs))

    def _invoke_callbacks(self, record_metadata):
        """Invoke all stored success callbacks with the record metadata."""
        for fn, args, kwargs in self._callbacks:
            fn(*args, record_metadata, **kwargs)

    def _invoke_errbacks(self, exception):
        """Invoke all stored error callbacks with the exception."""
        for fn, args, kwargs in self._errbacks:
            fn(*args, exception, **kwargs)


def _confluent_send_wrapper(producer, topic, key, value):
    """
    Wrap produce() to accept key/value, apply value_serializer, register a
    delivery_report callback that fires success/failure handlers, and return
    a Future-like object compatible with add_callback/add_errback.

    kafka-python's send() returns a Future with add_callback/add_errback.
    confluent-kafka's produce() takes a callback (delivery_report) with
    signature (err, msg). This wrapper applies the stored value_serializer
    to the value, calls produce() with a delivery_report that translates
    the result into calls on the returned _ConfluentFuture.
    """
    # Apply the value serializer if present
    if hasattr(producer, "_value_serializer") and producer._value_serializer is not None:
        value = producer._value_serializer(value)

    # Serialize key if it's not None and not already bytes
    if key is not None and not isinstance(key, bytes):
        key = str(key).encode("utf-8")

    future = _ConfluentFuture()

    def delivery_report(err, msg):
        # This callback is called from librdkafka's internal thread via poll()
        if err is not None:
            future._invoke_errbacks(err)
        else:
            # Create a record_metadata-like object compatible with kafka-python
            # kafka-python's RecordMetadata has: topic, partition, offset, timestamp, serialized_key_size, serialized_value_size
            class RecordMetadata:
                def __init__(self, msg):
                    self.topic = msg.topic()
                    self.partition = msg.partition()
                    self.offset = msg.offset()
                    self.timestamp = msg.timestamp()[1] if msg.timestamp()[0] else None
                    self.serialized_key_size = len(msg.key()) if msg.key() else 0
                    self.serialized_value_size = len(msg.value()) if msg.value() else 0

            future._invoke_callbacks(RecordMetadata(msg))

    # Produce the message with the delivery report callback
    producer.produce(topic, key=key, value=value, callback=delivery_report)
    # Trigger any pending delivery reports
    producer.poll(0)

    return future


def _producer_flush_wrapper(producer, timeout):
    """
    Delegate flush(timeout) to the underlying confluent-kafka Producer instance.

    kafka-python's flush(timeout) takes timeout in seconds.
    confluent-kafka's flush(timeout) takes timeout in milliseconds.
    This wrapper converts seconds to milliseconds.
    """
    # confluent-kafka expects timeout in milliseconds
    producer.flush(timeout=int(timeout * 1000))
class KafkaLocust(Locust):
    abstract = True
    wait_time = constant(0)
    # overload these values in your subclass
    bootstrap_servers = None
    value_serializer = str.encode

    def __init__(self, environment):
        super().__init__(environment)
        self.client = KafkaClient(
            environment=environment, bootstrap_servers=self.bootstrap_servers, value_serializer=self.value_serializer
        )

    def teardown(self):
        # this should actually be run for every locust instead of just once, but it is better than nothing...
        self.client.producer.flush(timeout=5)


class KafkaClient:
    def __init__(self, *, environment, bootstrap_servers, value_serializer):
        self.environment = environment
        self.producer = _create_confluent_producer(bootstrap_servers=bootstrap_servers, value_serializer=value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        future = self.producer.send(topic, key=key, value=message)
        future.add_callback(self.__handle_success, start_time, topic=topic, response_length=response_length)
        future.add_errback(self.__handle_failure, start_time, topic=topic, response_length=response_length)

    def __handle_success(self, start_time, record_metadata, *, topic, response_length=None):
        self.environment.events.request_success.fire(
            request_type="ENQUEUE",
            name=topic,
            response_time=int((time.time() - start_time) * 1000),
            response_length=response_length if response_length else record_metadata.serialized_value_size,
        )

    def __handle_failure(self, exception, start_time, *, topic, response_length=None):
        self.environment.events.request_failure.fire(
            request_type="ENQUEUE",
            name=topic,
            response_time=int((time.time() - start_time) * 1000),
            response_length=response_length,
            exception=exception,
        )
