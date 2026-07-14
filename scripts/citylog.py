"""
citylog.py — logger unico y conciso para la skill maps-to-3d (modulo puro, sin bpy).

get_logger() devuelve un logging.Logger configurado una sola vez (StreamHandler,
formato corto) con nivel tomado de la variable de entorno MAPS3D_LOGLEVEL
(default INFO). Es idempotente: llamarlo N veces no duplica handlers.
"""
import logging
import os

_FORMAT = "%(levelname)s %(name)s: %(message)s"


def _level_from_env():
    """Nivel de log desde MAPS3D_LOGLEVEL (nombre o numero); default INFO."""
    raw = os.environ.get("MAPS3D_LOGLEVEL", "INFO").strip()
    if not raw:
        return logging.INFO
    if raw.isdigit():
        return int(raw)
    return getattr(logging, raw.upper(), logging.INFO)


def get_logger(name="maps3d"):
    """
    Devuelve el logger `name`, configurandolo una sola vez.

    El nivel se re-lee del entorno en cada llamada (util para tests), pero el
    handler se agrega una unica vez para no duplicar la salida.
    """
    logger = logging.getLogger(name)
    logger.setLevel(_level_from_env())
    if not getattr(logger, "_maps3d_configured", False):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
        logger.propagate = False
        logger._maps3d_configured = True
    return logger
