# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from concurrent.futures import Future
from confluent_kafka import Producer
from locust import Locust
from locust.wait_time import constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with mapped configuration.

    kafka-python KafkaProducer accepts 'bootstrap_servers' and 'value_serializer'
    as separate parameters. confluent-kafka Producer expects a config dict with
    'bootstrap.servers' and does not have a built-in value_serializer - we must
    apply serialization manually before calling produce().

    Returns a wrapper object that provides a send() method compatible with
    kafka-python's KafkaProducer.send() interface.
    """
    config = {"bootstrap.servers": bootstrap_servers}
    producer = Producer(config)

    class ProducerWrapper:
        def __init__(self, producer, value_serializer):
            self._producer = producer
            self._value_serializer = value_serializer

        def send(self, topic, key=None, value=None):
            """
            Send a message, returning a Future-like object.

            kafka-python: send() returns a Future that resolves to RecordMetadata.
            confluent-kafka: produce() is async with a delivery callback (err, msg).
            We wrap produce() to return a Future that mimics kafka-python's behavior.
            """
            # Serialize value if serializer provided (kafka-python does this automatically)
            serialized_value = self._value_serializer(value) if value is not None else None
            serialized_key = key.encode() if isinstance(key, str) else key

            future = Future()

            def delivery_callback(err, msg):
                # This callback runs in the producer's poll() thread
                if err:
                    future.set_exception(err)
                else:
                    # Create a simple object mimicking RecordMetadata
                    class RecordMetadata:
                        def __init__(self, msg):
                            self.topic = msg.topic()
                            self.partition = msg.partition()
                            self.offset = msg.offset()
                            self.serialized_value_size = len(msg.value()) if msg.value() else 0

                    future.set_result(RecordMetadata(msg))

            self._producer.produce(
                topic,
                key=serialized_key,
                value=serialized_value,
                on_delivery=delivery_callback
            )
            return future

        def flush(self, timeout=5):
            """Flush pending messages, waiting up to timeout seconds."""
            self._producer.flush(timeout=timeout)

    return ProducerWrapper(producer, value_serializer)


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
        future.add_done_callback(self._handle_done, start_time, topic=topic, response_length=response_length)

    def _handle_done(self, future, start_time, *, topic, response_length=None):
        """
        Unified callback handler for both success and failure.

        confluent-kafka's delivery callback provides (err, msg) - we adapt this
        to call the original __handle_success/__handle_failure with expected
        arguments (record_metadata vs msg, exception vs err).
        """
        try:
            record_metadata = future.result()
            self.__handle_success(start_time, record_metadata, topic=topic, response_length=response_length)
        except Exception as exception:
            self.__handle_failure(exception, start_time, topic=topic, response_length=response_length)

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
