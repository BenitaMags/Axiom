"""
.. codeauthor:: Tsuyoshi Hombashi <tsuyoshi.hombashi@gmail.com>
"""
import loguru
import simplesqlite
import subprocrunner
import sys
from loguru import logger as loguru_logger

def create_loguru_logger(name: str):
    """
    Create a loguru-compatible logger object that mimics logbook.Logger interface.

    Original: logbook.Logger(name) -> logbook.Logger instance with enable(), disable(),
    level property, and disabled attribute.
    New: Returns a wrapper object with the same interface backed by loguru.

    The wrapper provides:
    - enable(): enables logging output (clears disabled flag)
    - disable(): disables logging output (sets disabled flag)
    - disabled: property returning bool for enabled/disabled state
    - level: property getter/setter for log level (uses loguru's level names)
    """

    class LoguruLoggerWrapper:

        def __init__(self, name: str):
            self._name = name
            self._disabled = False
            self._level = 'INFO'
            loguru_logger.remove()
            self._handler_id = loguru_logger.add(sys.stderr, level=self._level, filter=lambda record: not self._disabled and record['extra'].get('name') == self._name, format='{time} {level} {message}')

        def enable(self):
            """Enable logging output (mimics logbook.Logger.enable())."""
            self._disabled = False

        def disable(self):
            """Disable logging output (mimics logbook.Logger.disable())."""
            self._disabled = True

        @property
        def disabled(self):
            """Return True if logger is disabled (mimics logbook.Logger.disabled)."""
            return self._disabled

        @disabled.setter
        def disabled(self, value: bool):
            self._disabled = bool(value)

        @property
        def level(self):
            """Get current log level as logbook-compatible integer."""
            level_map = {'TRACE': 5, 'DEBUG': 10, 'INFO': 20, 'SUCCESS': 25, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
            return level_map.get(self._level, 20)

        @level.setter
        def level(self, value):
            """Set log level from logbook integer or string."""
            reverse_map = {5: 'TRACE', 10: 'DEBUG', 20: 'INFO', 25: 'SUCCESS', 30: 'WARNING', 40: 'ERROR', 50: 'CRITICAL'}
            if isinstance(value, int):
                level_name = reverse_map.get(value, 'INFO')
            else:
                level_name = str(value).upper()
            self._level = level_name
            loguru_logger.remove(self._handler_id)
            self._handler_id = loguru_logger.add(sys.stderr, level=self._level, filter=lambda record: not self._disabled and record['extra'].get('name') == self._name, format='{time} {level} {message}')

        def __getattr__(self, name):
            return getattr(loguru_logger.bind(name=self._name), name)
    return LoguruLoggerWrapper(name)

def get_loguru_level_name(level):
    """
    Validate log level against loguru's accepted level names/values and raise LookupError if invalid,
    mimicking logbook.get_level_name() behavior.

    Original: logbook.get_level_name(level) -> returns level name string or raises LookupError.
    New: Accepts logbook-level integers or loguru level names, validates against loguru levels,
    returns the loguru level name string, raises LookupError if invalid.
    """
    logbook_levels = {0: 'NOTSET', 5: 'TRACE', 10: 'DEBUG', 20: 'INFO', 25: 'SUCCESS', 30: 'WARNING', 40: 'ERROR', 50: 'CRITICAL'}
    loguru_levels = {'TRACE', 'DEBUG', 'INFO', 'SUCCESS', 'WARNING', 'ERROR', 'CRITICAL'}
    if isinstance(level, int):
        if level == 0:
            return 'NOTSET'
        if level not in logbook_levels:
            raise LookupError(f'Invalid log level: {level}')
        return logbook_levels[level]
    elif isinstance(level, str):
        upper = level.upper()
        if upper in loguru_levels or upper == 'NOTSET':
            return upper
        raise LookupError(f'Invalid log level: {level}')
    else:
        raise LookupError(f'Invalid log level type: {type(level)}')

def _disable_logger(l):
    """
    Disable a loguru-compatible logger wrapper.

    Original: Called l.disable() and fell back to l.disabled = True for logbook<1.0.0.
    New: Directly calls the wrapper's disable() method and sets disabled flag.
    """
    l.disable()
    l.disabled = True

def set_logger(is_enable):
    """
    Enable or disable the module logger and dependent loggers.

    Original: Used logger.disabled, logger.enable(), logger.disabled = False,
    and _disable_logger() for logbook compatibility.
    New: Uses the wrapper's disabled property, enable(), and disable() methods directly.
    """
    if is_enable == logger.disabled:
        return
    if is_enable:
        logger.enable()
        logger.disabled = False
    else:
        _disable_logger(logger)
    simplesqlite.set_logger(is_enable)
    subprocrunner.set_logger(is_enable)

def set_log_level(log_level):
    """
    Set logging level of this module.

    Original: Validated with logbook.get_level_name(), compared with logger.level,
    called set_logger(), set logger.level = log_level.
    New: Uses get_loguru_level_name() for validation, wrapper's level property for get/set.
    """
    get_loguru_level_name(log_level)
    if log_level == logger.level:
        return
    if log_level == 0:
        set_logger(is_enable=False)
    else:
        set_logger(is_enable=True)
    logger.level = log_level
    simplesqlite.set_log_level(log_level)
    subprocrunner.set_log_level(log_level)
logger = create_loguru_logger('tcconfig')
_disable_logger(logger)