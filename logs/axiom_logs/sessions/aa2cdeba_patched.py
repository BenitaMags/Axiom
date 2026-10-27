import time
from confluent_kafka import Producer
from locust import Locust
from locust import constant

def _create_confluent_producer(bootstrap_servers, value_serializer):
    """
    Create a confluent-kafka Producer with translated config and wrap it to apply
    value_serializer to message values before produce().

    kafka-python KafkaProducer accepts 'bootstrap_servers' and 'value_serializer'
    as keyword arguments. confluent-kafka Producer expects 'bootstrap.servers'
    in a config dict and has no built-in serializer — serialization must be done
    manually before calling produce().

    This wrapper creates a Producer with the translated config and stores the
    value_serializer on the producer instance for use by _confluent_send_wrapper.
    """
    config = {'bootstrap.servers': bootstrap_servers}
    producer = Producer(config)
    producer._value_serializer = value_serializer
    return producer

class _ConfluentFuture:
    """
    Future-like object compatible with kafka-python's Future interface.

    kafka-python's Future has add_callback() and add_errback() methods that
    register callbacks to be invoked when the send succeeds or fails.
    confluent-kafka uses a delivery callback with signature (err, msg) passed
    to produce(). This class bridges the two by storing success/error callbacks
    and invoking them appropriately from the delivery callback.
    """

    def __init__(self):
        self._callbacks = []
        self._errbacks = []

    def add_callback(self, fn, *args, **kwargs):
        """Store a success callback with its bound arguments."""
        self._callbacks.append((fn, args, kwargs))

    def add_errback(self, fn, *args, **kwargs):
        """Store an error callback with its bound arguments."""
        self._errbacks.append((fn, args, kwargs))

    def _invoke_callbacks(self, record_metadata):
        """Invoke all stored success callbacks with the record metadata."""
        for fn, args, kwargs in self._callbacks:
            fn(*args, record_metadata, **kwargs)

    def _invoke_errbacks(self, exception):
        """Invoke all stored error callbacks with the exception."""
        for fn, args, kwargs in self._errbacks:
            fn(*args, exception, **kwargs)

def _confluent_send_wrapper(producer, topic, key=None, value=None):
    """
    Serialize key/value using the stored value_serializer, call producer.produce()
    with a delivery callback that mimics Future.add_callback/add_errback behavior,
    and return a Future-like object compatible with the existing callback chain.

    kafka-python: producer.send() returns a Future with add_callback/add_errback.
    confluent-kafka: producer.produce() takes a callback(err, msg) and returns None.

    This wrapper applies the value_serializer (and key serializer if needed),
    calls produce() with a delivery callback that invokes the appropriate
    callbacks on the returned _ConfluentFuture, and returns that future.
    """
    future = _ConfluentFuture()
    serialized_value = None
    if value is not None and hasattr(producer, '_value_serializer') and producer._value_serializer:
        serialized_value = producer._value_serializer(value)
    else:
        serialized_value = value
    serialized_key = key
    if key is not None and isinstance(key, str):
        serialized_key = key.encode('utf-8')

    def delivery_callback(err, msg):
        if err:
            future._invoke_errbacks(err)
        else:
            future._invoke_callbacks(msg)
    producer.produce(topic, key=serialized_key, value=serialized_value, callback=delivery_callback)
    producer.poll(0)
    return future

def _extract_serialized_size(msg):
    """
    Extract serialized value size from confluent-kafka Message object.

    kafka-python's RecordMetadata has a 'serialized_value_size' attribute.
    confluent-kafka's Message has a value() method returning the raw bytes.

    This wrapper returns len(msg.value()) if msg.value() is not None else 0.
    """
    if msg is None:
        return 0
    value = msg.value()
    return len(value) if value is not None else 0

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
        self.producer = _create_confluent_producer(bootstrap_servers, value_serializer)

    def send(self, *, topic: str, key=None, message=None, response_length=None):
        start_time = time.time()
        future = _confluent_send_wrapper(self.producer, topic, key=key, value=message)
        future.add_callback(self.__handle_success, start_time, topic=topic, response_length=response_length)
        future.add_errback(self.__handle_failure, start_time, topic=topic, response_length=response_length)

    def __handle_success(self, start_time, record_metadata, *, topic, response_length=None):
        self.environment.events.request_success.fire(request_type='ENQUEUE', name=topic, response_time=int((time.time() - start_time) * 1000), response_length=response_length if response_length else _extract_serialized_size(record_metadata))

    def __handle_failure(self, exception, start_time, *, topic, response_length=None):
        self.environment.events.request_failure.fire(request_type='ENQUEUE', name=topic, response_time=int((time.time() - start_time) * 1000), response_length=response_length, exception=exception)