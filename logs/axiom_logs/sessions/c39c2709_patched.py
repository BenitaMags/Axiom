# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from confluent_kafka import Producer
from locust import Locust
try:
    from locust.wait_time import constant
except ImportError:
    from locust import constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with translated config.

    kafka-python KafkaProducer accepts:
      - bootstrap_servers: str or list of str
      - value_serializer: callable that returns bytes

    confluent-kafka Producer accepts:
      - bootstrap.servers: str (comma-separated)
      - value.serializer: callable that returns bytes (but expects a different interface)

    This wrapper maps the config parameters and ensures the serializer returns bytes.
    """
    # Convert bootstrap_servers to bootstrap.servers format
    if isinstance(bootstrap_servers, list):
        bootstrap_servers_str = ",".join(bootstrap_servers)
    else:
        bootstrap_servers_str = bootstrap_servers

    # confluent-kafka doesn't have a built-in value.serializer config like kafka-python.
    # We'll handle serialization manually in the send wrapper.
    config = {
        "bootstrap.servers": bootstrap_servers_str,
    }
    return Producer(config), value_serializer


def _confluent_send_with_callbacks(producer, serializer, topic, key, value, on_success, on_failure, start_time, response_length):
    """
    Wrap confluent-kafka Producer.produce() with on_delivery callback that mimics Future behavior.

    kafka-python: producer.send() returns a Future with add_callback/add_errback methods.
    confluent-kafka: producer.produce() takes an on_delivery callback with signature (err, msg).

    This wrapper:
    1. Serializes the value using the provided serializer
    2. Calls produce() with an on_delivery callback that translates (err, msg) to
       success/failure callbacks matching the original Future interface
    3. Calls poll(0) to trigger any ready callbacks (non-blocking)
    """
    # Serialize the value if serializer is provided
    if serializer and value is not None:
        serialized_value = serializer(value)
    else:
        serialized_value = value

    # Serialize key if provided
    if key is not None and serializer:
        serialized_key = serializer(key)
    else:
        serialized_key = key

    def on_delivery(err, msg):
        # This callback is called from poll() - translate to success/failure
        if err is not None:
            on_failure(err, start_time, topic=topic, response_length=response_length)
        else:
            on_success(start_time, msg, topic=topic, response_length=response_length)

    # Produce the message with the on_delivery callback
    producer.produce(
        topic,
        key=serialized_key,
        value=serialized_value,
        on_delivery=on_delivery,
    )
    # Poll to trigger delivery callbacks (non-blocking)
    producer.poll(0)


def _get_serialized_size(msg):
    """
    Extract serialized value size from confluent-kafka Message.

    kafka-python: record_metadata.serialized_value_size
    confluent-kafka: msg.value() returns the serialized bytes, so we use len()

    Returns the length of the serialized value, or 0 if None.
    """
    value = msg.value()
    return len(value) if value is not None else 0
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
        # Use wrapper to translate kafka-python config to confluent-kafka config
        self.producer, self.value_serializer = _create_confluent_producer(bootstrap_servers, value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        _confluent_send_with_callbacks(
            producer=self.producer,
            serializer=self.value_serializer,
            topic=topic,
            key=key,
            value=message,
            on_success=self.__handle_success,
            on_failure=self.__handle_failure,
            start_time=start_time,
            response_length=response_length,
        )

    def __handle_success(self, start_time, msg, *, topic, response_length=None):
        self.environment.events.request_success.fire(
            request_type="ENQUEUE",
            name=topic,
            response_time=int((time.time() - start_time) * 1000),
            response_length=response_length if response_length else _get_serialized_size(msg),
        )

    def __handle_failure(self, exception, start_time, *, topic, response_length=None):
        self.environment.events.request_failure.fire(
            request_type="ENQUEUE",
            name=topic,
            response_time=int((time.time() - start_time) * 1000),
            response_length=response_length,
            exception=exception,
        )