from django.core.cache import caches


def test_default_cache_is_file_based():
    backend = caches["default"]
    assert type(backend).__name__ == "FileBasedCache"


def test_default_cache_round_trips_a_value():
    backend = caches["default"]
    backend.set("metrics-cache-probe", {"value": 1}, timeout=60)
    assert backend.get("metrics-cache-probe") == {"value": 1}
