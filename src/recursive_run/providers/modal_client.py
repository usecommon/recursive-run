"""Pinned Modal SDK transport using the managed machine's trusted system CA bundle.

Modal's default grpclib channel uses certifi directly, omitting the managed proxy CA.
This small version-pinned client hook passes a verified SSLContext to ModalChannel.
No trust-store bytes, provider credentials, or proxy routing are modified.
"""

import asyncio
import importlib.metadata
import os
import ssl
from urllib.parse import urlparse

import grpclib.events
from modal.client import _Client, _get_metadata
from modal._utils.grpc_utils import ModalChannel, create_channel_config
from modal_proto import api_pb2

from recursive_run.ledger import Blocked


class SystemTrustModalClient(_Client):
    def __init__(self):
        if importlib.metadata.version("modal") != "1.6.0":
            raise Blocked("Modal SDK differs from pinned transport version")
        token_id = os.environ.get("MODAL_TOKEN_ID")
        token_secret = os.environ.get("MODAL_TOKEN_SECRET")
        if not token_id or not token_secret:
            raise Blocked("Modal token bindings missing")
        super().__init__(
            "https://api.modal.com", api_pb2.CLIENT_TYPE_CLIENT, (token_id, token_secret)
        )
        self._system_channels = {}

    async def _get_channel(self, server_url):
        parsed = urlparse(server_url)
        if parsed.scheme != "https" or parsed.hostname not in {"api.modal.com", "api.modal2.com"}:
            raise Blocked("unreviewed Modal RPC destination")
        channel = self._system_channels.get(server_url)
        if channel is None:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["h2"])
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            channel = ModalChannel(
                parsed.hostname, parsed.port or 443, ssl=context, config=create_channel_config()
            )
            metadata = _get_metadata(
                self.client_type, self._credentials, self.version, self._oauth_credentials
            )

            async def inject(event):
                for key, value in metadata.items():
                    event.metadata[key] = value
                event.metadata["x-modal-host"] = parsed.hostname

            grpclib.events.listen(channel, grpclib.events.SendRequest, inject)
            self._system_channels[server_url] = channel
        return channel

    async def _close(self, prep_for_restore=False):
        try:
            await super()._close(prep_for_restore)
        finally:
            for channel in self._system_channels.values():
                channel.close()
            self._system_channels.clear()


async def authenticate():
    """Read-only ClientHello RPC, explicitly no implicit RPC retry."""
    from google.protobuf import empty_pb2

    client = SystemTrustModalClient()
    try:
        await asyncio.wait_for(client._open(), 15)
        await asyncio.wait_for(
            client._stub.ClientHello(empty_pb2.Empty(), retry=None, timeout=15), 20
        )
        return {"provider": "Modal", "authenticated": True, "paid_workers": 0}
    finally:
        await client._close()
