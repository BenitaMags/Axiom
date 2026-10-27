# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from concurrent.futures import Future
from confluent_kafka import Producer
from locust import Locust
from locust import constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with mapped config.

    kafka-python's KafkaProducer accepts 'bootstrap_servers' and a 'value_serializer' callable.
    confluent-kafka's Producer expects 'bootstrap.servers' and does not have built-in serialization;
    it expects bytes for key/value. This wrapper maps the config and stores the serializer
    on the producer instance for later use by _produce_with_future.
    """
    config = {"bootstrap.servers": bootstrap_servers}
    producer = Producer(config)
    # Store serializer on producer for use in _produce_with_future
    producer._value_serializer = value_serializer
    producer._key_serializer = lambda k: k if k is None else str(k).encode("utf-8")
    return producer


def _produce_with_future(producer, topic, key, value):
    """
    Serialize key/value using stored serializers, call producer.produce() with on_delivery
    callback that resolves a concurrent.futures.Future, return that Future so
    add_callback/add_errback chain works.

    kafka-python's producer.send() returns a Future-like object with add_callback/add_errback.
    confluent-kafka's producer.produce() takes an on_delivery callback and returns None.
    This wrapper creates a concurrent.futures.Future, calls produce() with an on_delivery
    that resolves/rejects that Future, and returns it. The Future supports add_callback/add_errback
    with the same signature as kafka-python's Future.
    """
    future = Future()

    # Serialize key and value using stored serializers
    serialized_key = producer._key_serializer(key) if key is not None else None
    serialized_value = producer._value_serializer(value) if value is not None else None

    def on_delivery(err, msg):
        # This callback runs in the poll() thread, so we need to set result/exception
        # on the future. Since Future is thread-safe for set_result/set_exception, this works.
        if err:
            future.set_exception(err)
        else:
            # Create a simple object with serialized_value_size attribute to match kafka-python's RecordMetadata
            class RecordMetadata:
                def __init__(self, msg):
                    self.topic = msg.topic()
                    self.partition = msg.partition()
                    self.offset = msg.offset()
                    self.serialized_key_size = len(msg.key()) if msg.key() else 0
                    self.serialized_value_size = len(msg.value()) if msg.value() else 0

            future.set_result(RecordMetadata(msg))

    producer.produce(topic, key=serialized_key, value=serialized_value, on_delivery=on_delivery)
    return future


def _flush_with_poll(producer, timeout):
    """
    Call producer.poll(0) in a loop to drain delivery callbacks, then call
    producer.flush(timeout), ensuring all callbacks fire before returning.

    kafka-python's producer.flush() blocks until all messages are delivered and
    callbacks are executed (handled by a background thread). confluent-kafka's
    producer.flush() only waits for messages to be delivered to the broker but
    does not invoke delivery callbacks - those are invoked via poll(). This wrapper
    polls to drain callbacks before and after flush to ensure all callbacks fire.
    """
    # Drain any pending callbacks before flush
    producer.poll(0)
    # Flush with timeout (in seconds)
    producer.flush(timeout=timeout)
# Drain callbacks after flush to ensure all delivery reports are processed
    producer.poll(0)


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
        _flush_with_poll(self.client.producer, timeout=5)

class KafkaClient:
    def __init__(self, *, environment, bootstrap_servers, value_serializer):
        self.environment = environment
        self.producer = _create_confluent_producer(bootstrap_servers=bootstrap_servers, value_serializer=value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        future = _produce_with_future(self.producer, topic, key, message)
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