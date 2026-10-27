#!/usr/bin/env python3
""" From here we start the app in debug mode. """
import gevent.monkey


def _gevent_monkey_patch_wrapper():
    """
    Wrapper to replicate eventlet.monkey_patch() behavior using gevent.

    eventlet.monkey_patch() patches standard library modules (socket, time,
    threading, etc.) to be cooperative. By default it patches all modules.

    gevent.monkey.patch_all() does the same for gevent. This wrapper calls
    patch_all() with default arguments which patches all standard modules,
    matching eventlet's default behavior.
    """
    gevent.monkey.patch_all()


_gevent_monkey_patch_wrapper()
from app import create_app, socketio  # noqa
app = create_app()

if __name__ == "__main__":
    socketio.run(app, debug=True)
