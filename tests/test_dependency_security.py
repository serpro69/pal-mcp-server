"""Security-sensitive dependency contracts retained by the supported lock set."""

import ssl

import pytest
from anyio.streams.tls import TLSStream


async def test_tls_hostname_uses_idna2008_before_certificate_matching(monkeypatch):
    async def no_network_handshake(self, method):
        pass

    # Inspect the real SSLObject's peer identity without doing network I/O.
    # Certificate-chain validation is not established by this serialization test.
    monkeypatch.setattr(TLSStream, "_call_sslobject_method", no_network_handshake)
    context = ssl.create_default_context()
    stream = await TLSStream.wrap(object(), hostname="faß.de", ssl_context=context)
    assert stream._ssl_object.server_hostname == "xn--fa-hia.de"
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


async def test_invalid_tls_hostname_fails_before_transport_io():
    # object() has no I/O methods: reaching transport work would fail differently.
    with pytest.raises(UnicodeError):
        await TLSStream.wrap(object(), hostname="☃.example", ssl_context=ssl.create_default_context())
