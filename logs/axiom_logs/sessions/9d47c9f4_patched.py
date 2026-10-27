# inspired by https://medium.com/locust-io-experiments/locust-experiments-locust-meets-kafka-b9d9e6e49537
import time
from confluent_kafka import Producer
from locust import User as Locust, constant


def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent_kafka.Producer with config mapping bootstrap_servers -> bootstrap.servers,
    and return a wrapper object that provides a send() method accepting value_serializer
    to serialize messages before calling produce().

    kafka-python's KafkaProducer accepts bootstrap_servers and value_serializer as parameters.
    confluent-kafka's Producer expects a config dict with 'bootstrap.servers' key.
    This wrapper maps the parameters and stores the serializer for use in send().
    """
    config = {"bootstrap.servers": bootstrap_servers}
    producer = Producer(config)

    class ProducerWrapper:
        def __init__(self, producer, value_serializer):
            self._producer = producer
            self._value_serializer = value_serializer

        def send(self, topic, key=None, value=None):
            """
            Serialize value using stored serializer and call produce() with on_delivery callback.
            Returns a future-like object compatible with kafka-python's Future interface.
            """
            # Serialize the value if a serializer is provided
            if self._value_serializer and value is not None:
                serialized_value = self._value_serializer(value)
            else:
                serialized_value = value

            # Serialize key if provided
            if key is not None and isinstance(key, str):
                key = key.encode("utf-8")

            # Create a future-like object to match kafka-python's interface
            from concurrent.futures import Future

            future = Future()

            def on_delivery(err, msg):
                if err is not None:
                    future.set_exception(err)
                else:
                    future.set_result(msg)

            self._producer.produce(topic, key=key, value=serialized_value, on_delivery=on_delivery)
            return future

        def flush(self, timeout=None):
            """Flush the producer, polling for delivery reports."""
            return self._producer.flush(timeout=timeout)

    return ProducerWrapper(producer, value_serializer)


def _confluent_send_wrapper(producer, topic, key, value, value_serializer, on_success, on_failure, start_time, response_length=None):
    """
    Wrap produce() to accept key, value, topic; serialize value using stored serializer;
    call produce() with on_delivery callback that translates (err, msg) to success/failure
    events matching the original Future callback interface.

    kafka-python's Future.add_callback/add_errback pattern is replaced by confluent-kafka's
    on_delivery callback with signature (err, msg). This wrapper handles the translation:
    - On success (err is None): calls on_success with record_metadata (msg)
    - On failure (err is not None): calls on_failure with exception (err)

    Also extracts message size from msg.len() or len(msg.value()) for response_length.
    """
    # Serialize the value if a serializer is provided
    if value_serializer and value is not None:
        serialized_value = value_serializer(value)
    else:
        serialized_value = value

    # Serialize key if provided
    if key is not None and isinstance(key, str):
        key = key.encode("utf-8")

    def on_delivery(err, msg):
        if err is not None:
            on_failure(err, start_time, topic=topic, response_length=response_length)
        else:
            # Extract message size from msg.len() or len(msg.value())
            if response_length is None:
                try:
                    msg_size = msg.len()
                except Exception:
                    try:
                        msg_size = len(msg.value()) if msg.value() else 0
                    except Exception:
                        msg_size = 0
            else:
                msg_size = response_length
            on_success(start_time, msg, topic=topic, response_length=msg_size)

    producer._producer.produce(topic, key=key, value=serialized_value, on_delivery=on_delivery)


def _confluent_flush_wrapper(producer, timeout=5):
    """
    Wrap flush() to call poll(0) in a loop until all messages are delivered or timeout expires,
    then call the underlying flush(timeout).

    confluent-kafka's Producer.poll() must be called periodically for callbacks to fire.
    This wrapper ensures delivery reports are processed before flushing.
    """
    import time as _time

    start = _time.time()
    # Poll for delivery reports until timeout or all messages delivered
    while True:
        # Poll with 0 timeout to process any pending callbacks
        producer._producer.poll(0)
        # Check if there are any messages still in flight
        if producer._producer.outq_len() == 0:
            break
        if timeout is not None and (_time.time() - start) >= timeout:
            break
    # Final flush with remaining timeout
    remaining_timeout = timeout - (_time.time() - start) if timeout is not None else None
    if remaining_timeout is not None and remaining_timeout < 0:
        remaining_timeout = 0
    return producer._producer.flush(remaining_timeout if remaining_timeout is not None else timeout)
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
        self.producer = KafkaProducer(bootstrap_servers=bootstrap_servers, value_serializer=value_serializer)

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
