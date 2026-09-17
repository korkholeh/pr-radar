import httpx
import pytest
import respx


def test_unmocked_request_raises_instead_of_hitting_the_network():
    with pytest.raises(respx.models.AllMockedAssertionError, match="not mocked"):
        httpx.get("https://api.github.com/")
