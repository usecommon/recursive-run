import asyncio
import ssl

import pytest

from recursive_run.ledger import Blocked
from recursive_run.providers import modal_client


def test_transport_preserves_verified_system_trust_and_closes(monkeypatch):
    monkeypatch.setenv('MODAL_TOKEN_ID', 'test-id')
    monkeypatch.setenv('MODAL_TOKEN_SECRET', 'test-secret')
    channels = []

    class Channel:
        def __init__(self, host, port, *, ssl, config):
            self.host, self.port, self.context = host, port, ssl
            self.closed = False
            channels.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(modal_client, 'ModalChannel', Channel)
    monkeypatch.setattr(modal_client.grpclib.events, 'listen', lambda *args: None)

    async def exercise():
        client = modal_client.SystemTrustModalClient()
        channel = await client._get_channel('https://api.modal.com')
        assert channel is await client._get_channel('https://api.modal.com')
        assert channel.context.check_hostname
        assert channel.context.verify_mode == ssl.CERT_REQUIRED
        assert channel.context.minimum_version >= ssl.TLSVersion.TLSv1_2
        with pytest.raises(Blocked):
            await client._get_channel('https://unreviewed.example')
        await client._close()
        assert channel.closed
        assert not client._system_channels

    asyncio.run(exercise())
    assert len(channels) == 1


def test_transport_rejects_unpinned_sdk(monkeypatch):
    monkeypatch.setattr(modal_client.importlib.metadata, 'version', lambda name: '0.0.0')
    with pytest.raises(Blocked, match='pinned transport'):
        modal_client.SystemTrustModalClient()
