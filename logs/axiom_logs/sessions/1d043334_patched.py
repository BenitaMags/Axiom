# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from confluent_kafka import Producer
from locust import Locust
from locust import constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with equivalent config, and wrap send() to handle
    manual serialization and schedule poll() for callback execution.

    kafka-python's KafkaProducer accepts bootstrap_servers and value_serializer directly.
    confluent-kafka's Producer expects a config dict with 'bootstrap.servers'.
    The value_serializer must be applied manually before calling produce().
    """
    config = {
        'bootstrap.servers': bootstrap_servers,
        # Align defaults with kafka-python: acks=all, retries=MAX_INT
        'acks': 'all',
        'retries': 2147483647,
    }
    producer = Producer(config)
    # Attach the serializer for use by the send wrapper
    producer._value_serializer = value_serializer
    return producer


def _confluent_send_wrapper(producer, topic, key, value, on_delivery, start_time=None, response_length=None):
    """
    Wrap produce() to accept key/value, apply serialization, register a delivery callback
    that adapts (err, msg) to the existing __handle_success/__handle_failure signatures,
    and ensure poll() is called.

    kafka-python: send() returns a Future; callbacks added via add_callback/add_errback
    receive (record_metadata) or (exception).

    confluent-kafka: produce() takes a callback with signature (err, msg).
    - On success: err=None, msg is a Message object with .topic(), .partition(), .offset(), .len()
    - On failure: err is a KafkaError, msg is None

    This wrapper applies the value_serializer, calls produce() with an adapted callback,
    and calls poll(0) to trigger any ready callbacks.
    """
    # Apply serialization (kafka-python does this internally)
    if producer._value_serializer and value is not None:
        value = producer._value_serializer(value)
    if key is not None and not isinstance(key, bytes):
        key = str(key).encode('utf-8')

    def delivery_callback(err, msg):
        if err is not None:
            on_delivery(err, None)
        else:
            on_delivery(None, msg)

    producer.produce(topic, key=key, value=value, on_delivery=delivery_callback)
    # Poll to serve delivery callbacks (non-blocking)
    producer.poll(0)

def _adapt_record_metadata(msg):
    """
    Extract serialized value size from confluent-kafka Message object.

    kafka-python's record_metadata has .serialized_value_size attribute.
    confluent-kafka's Message has .len() method returning the message size in bytes.
    """
    class AdaptedRecordMetadata:
        def __init__(self, msg):
            self._msg = msg

        @property
        def serialized_value_size(self):
            return self._msg.len()

        @property
        def topic(self):
            return self._msg.topic()

        @property
        def partition(self):
            return self._msg.partition()

        @property
        def offset(self):
            return self._msg.offset()

    return AdaptedRecordMetadata(msg)

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
        self.producer = _create_confluent_producer(bootstrap_servers, value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        _confluent_send_wrapper(
            producer=self.producer,
            topic=topic,
            key=key,
            value=message,
            on_delivery=lambda err, msg: self.__handle_delivery(err, msg, start_time=start_time, topic=topic, response_length=response_length),
        )

    def __handle_delivery(self, err, msg, *, start_time, topic, response_length=None):
        if err is not None:
            self.__handle_failure(err, start_time, topic=topic, response_length=response_length)
        else:
            record_metadata = _adapt_record_metadata(msg)
            self.__handle_success(start_time, record_metadata, topic=topic, response_length=response_length)

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