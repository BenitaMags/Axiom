import time
from concurrent.futures import Future
from confluent_kafka import Producer
from locust import Locust
from locust.wait_time import constant

def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with 'bootstrap.servers' config and wrap it
    to accept a value_serializer callable that is applied to values in send().

    kafka-python's KafkaProducer accepts `bootstrap_servers` and `value_serializer`
    as separate constructor arguments. confluent-kafka's Producer only accepts a
    config dict with 'bootstrap.servers' and has no built-in serializer support.
    This wrapper creates the Producer with the correct config and stores the
    serializer for later use by _confluent_send_wrapper.
    """
    config = {'bootstrap.servers': bootstrap_servers}
    producer = Producer(config)
    producer._value_serializer = value_serializer
    return producer

class _FutureAdapter:
    """
    Future-like adapter that mimics kafka-python's Future interface with
    add_callback/add_errback methods.

    kafka-python's Future (from producer.send()) provides add_callback and
    add_errback methods that accept callbacks with specific signatures.
    confluent-kafka's produce() returns None and uses an on_delivery callback
    with signature (err, msg). This adapter stores success/error callbacks and
    invokes them from the on_delivery handler with adapted arguments.
    """

    def __init__(self, serialized_value_size):
        self._callbacks = []
        self._errbacks = []
        self._serialized_value_size = serialized_value_size
        self._result = None
        self._exception = None
        self._done = False

    def add_callback(self, fn, *args, **kwargs):
        """Store a success callback to be called when delivery succeeds."""
        self._callbacks.append((fn, args, kwargs))
        if self._done and self._exception is None:
            self._invoke_callbacks()
        return self

    def add_errback(self, fn, *args, **kwargs):
        """Store an error callback to be called when delivery fails."""
        self._errbacks.append((fn, args, kwargs))
        if self._done and self._exception is not None:
            self._invoke_errbacks()
        return self

    def _invoke_callbacks(self):
        """Invoke all stored success callbacks with adapted record_metadata."""
        record_metadata = type('RecordMetadata', (), {'serialized_value_size': self._serialized_value_size})()
        for fn, args, kwargs in self._callbacks:
            fn(*args, record_metadata, **kwargs)

    def _invoke_errbacks(self):
        """Invoke all stored error callbacks with the exception."""
        for fn, args, kwargs in self._errbacks:
            fn(self._exception, *args, **kwargs)

    def set_result(self, err, msg):
        """Called from on_delivery to complete the future."""
        self._done = True
        if err is not None:
            self._exception = err
            self._invoke_errbacks()
        else:
            self._result = msg
            self._invoke_callbacks()

def _confluent_send_wrapper(producer, topic, key=None, value=None):
    """
    Wrap produce() to apply value_serializer, attach on_delivery callback that
    mimics Future.add_callback/add_errback behavior, and return a Future-like
    object compatible with existing callback chain.

    kafka-python's producer.send() applies the value_serializer, sends the
    message, and returns a Future with add_callback/add_errback.
    confluent-kafka's producer.produce() requires pre-serialized bytes, takes
    an on_delivery callback with signature (err, msg), and returns None.
    This wrapper applies the serializer, computes the serialized size for
    record_metadata, and returns a _FutureAdapter.
    """
    if value is not None and hasattr(producer, '_value_serializer') and (producer._value_serializer is not None):
        serialized_value = producer._value_serializer(value)
    else:
        serialized_value = value
    serialized_value_size = len(serialized_value) if serialized_value is not None else 0
    future_adapter = _FutureAdapter(serialized_value_size)

    def on_delivery(err, msg):
        future_adapter.set_result(err, msg)
    producer.produce(topic, key=key, value=serialized_value, on_delivery=on_delivery)
    return future_adapter

class KafkaLocust(Locust):
    abstract = True
    wait_time = constant(0)
    bootstrap_servers = None
    value_serializer = str.encode

    def __init__(self, environment):
        super().__init__(environment)
        self.client = KafkaClient(environment=environment, bootstrap_servers=self.bootstrap_servers, value_serializer=self.value_serializer)

    def teardown(self):
        self.client.producer.flush(timeout=5)

class KafkaClient:

    def __init__(self, *, environment, bootstrap_servers, value_serializer):
        self.environment = environment
        self.producer = _create_confluent_producer(bootstrap_servers=bootstrap_servers, value_serializer=value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        future = _confluent_send_wrapper(self.producer, topic=topic, key=key, value=message)
        future.add_callback(self.__handle_success, start_time, topic=topic, response_length=response_length)
        future.add_errback(self.__handle_failure, start_time, topic=topic, response_length=response_length)

    def __handle_success(self, start_time, record_metadata, *, topic, response_length=None):
        self.environment.events.request_success.fire(request_type='ENQUEUE', name=topic, response_time=int((time.time() - start_time) * 1000), response_length=response_length if response_length else record_metadata.serialized_value_size)

    def __handle_failure(self, exception, start_time, *, topic, response_length=None):
        self.environment.events.request_failure.fire(request_type='ENQUEUE', name=topic, response_time=int((time.time() - start_time) * 1000), response_length=response_length, exception=exception)