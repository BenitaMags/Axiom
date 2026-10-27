# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from concurrent.futures import Future
from confluent_kafka import Producer
from locust import Locust
from locust.wait_time import constant


class _ProducerWrapper:
    """
    Wrapper around confluent_kafka.Producer to provide a kafka-python compatible interface.

    Key differences bridged:
    - confluent-kafka uses 'bootstrap.servers' config key vs kafka-python's 'bootstrap_servers'
    - confluent-kafka's produce() is non-blocking and uses delivery callbacks vs kafka-python's Future return
    - confluent-kafka requires explicit poll() calls for callbacks to fire (kafka-python uses background thread)
    - confluent-kafka Producer is not thread-safe (kafka-python's is)

    This wrapper:
    - Maps config keys to confluent-kafka format
    - Stores the value_serializer and applies it in send()
    - Returns a Future-like object compatible with add_callback/add_errback
    - Runs a background poll thread to deliver callbacks (matching kafka-python behavior)
    """

    def __init__(self, bootstrap_servers, value_serializer):
        self._value_serializer = value_serializer
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})
        self._poll_thread = None
        self._running = False
        self._start_poll_thread()

    def _start_poll_thread(self):
        """Start background thread to poll for delivery callbacks."""
        import threading

        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        """Background loop that polls for delivery callbacks."""
        while self._running:
            self._producer.poll(0.1)

    def send(self, topic, key=None, value=None):
        """
        Send a message and return a Future-like object.

        confluent-kafka's produce() takes a callback; we wrap it in a Future
        to match kafka-python's API (add_callback/add_errback).
        """
        future = Future()

        def delivery_callback(err, msg):
            if err:
                future.set_exception(err)
            else:
                # Create a mock record_metadata with serialized_value_size
                class RecordMetadata:
                    def __init__(self, msg):
                        self.topic = msg.topic()
                        self.partition = msg.partition()
                        self.offset = msg.offset()
                        # Estimate serialized size
                        self.serialized_value_size = len(msg.value()) if msg.value() else 0

                future.set_result(RecordMetadata(msg))

        # Serialize the value if serializer provided
        serialized_value = self._value_serializer(value) if value is not None else None
        serialized_key = self._value_serializer(key) if key is not None else None

        self._producer.produce(
            topic, key=serialized_key, value=serialized_value, callback=delivery_callback
        )
        return future

    def flush(self, timeout=None):
        """Flush pending messages, matching kafka-python's flush API."""
        self._producer.flush(timeout=timeout)

    def close(self):
        """Stop the poll thread and flush."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        self._producer.flush(timeout=5)


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Factory function to create a confluent-kafka producer wrapper.

    kafka-python's KafkaProducer accepts bootstrap_servers and value_serializer
    as keyword arguments. confluent-kafka's Producer uses a config dict with
    'bootstrap.servers' and has no built-in serializer.

    This wrapper handles the config mapping and provides a send() method that
    serializes values and returns a Future compatible with add_callback/add_errback.
    """
    return _ProducerWrapper(bootstrap_servers, value_serializer)


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