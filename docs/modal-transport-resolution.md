# Modal transport investigation — 2026-10-02

## Update — 2026-10-08

The managed proxy's own documentation (`/root/.ccr/README.md`, "Not supported through the proxy") lists **gRPC / HTTP/2-only APIs** as unsupported and says to report rather than work around them. That matches the missing-trailer evidence below and turns recommendation 1 (fix the managed path) into a platform request with no committed fix. Supplying Modal credentials does not change this. The realistic routes are recommendation 2 (run the controller on a host with a native Modal gRPC path, such as an operator workstation or a VM) or a runner whose control plane is plain HTTPS.

## Finding and confidence

The current managed HTTPS path is incompatible with successful Modal gRPC responses. Both `api.modal.com` and `api.modal2.com` return HTTP/2 response headers and protobuf data, followed by stream end, without observed gRPC status trailers. Modal's grpclib client then waits for trailing metadata until its deadline.

**High confidence:** the missing trailing status explains the timeout. **Strong inference:** a proxy or intermediary drops trailers. We cannot locate the exact component or exclude a service-side issue without platform/Modal traces or a separately authorized, known-good comparison path. The earlier statement that the cloud proxy specifically needs fixing was more definitive than the evidence supports.

OpenAI's read-only model lookup succeeds. Runtime revision 7 reports all three credential bindings ready. A valid Modal protobuf body does not establish RPC success or authorization; those still require a valid gRPC completion status. This research starts no apps, sandboxes or model generations.

## Evidence

| Probe | Observed result | Implication |
|---|---|---|
| Default Modal grpclib TLS context | Certificate verification error | Its certifi roots omit the managed CA |
| System-root SSL context, TLS verified, inherited proxy | TLS/HTTP2 negotiation succeeds | Trust issue resolved independently of gRPC completion |
| SDK 1.6.0, ClientHello, primary host | HTTP 200, data, stream end; no TrailersReceived; deadline timeout | Successful-response path is incomplete |
| Same RPC, secondary host | Same event sequence and timeout | Host switching does not resolve it |
| HTTPX HTTP/1.1 and HTTP/2 ClientHello | HTTP 200, application/grpc, 14-byte body; no status header | HTTP version change alone does not resolve it; HTTPX alone cannot prove trailer absence |
| Nonexistent diagnostic RPC, both HTTP versions | HTTP 200, grpc-status 12 in initial headers, empty body | Early/trailers-only errors survive; status headers are not universally removed |
| gRPC-Web binary/text request types | Native application/grpc response; no gRPC-Web status frame | These request switches do not provide an alternate transport |
| grpclib source inspection | recv_trailers waits on trailers_received | Explains timeout after a response that ends without trailing headers |

The direct hyper-h2 event observations, rather than HTTPX's header API, establish that trailing HEADERS frames were not delivered to the client. No secret values, request metadata, response bodies or arbitrary exception messages were logged in the diagnostic receipt.

## Research on supported alternatives

Modal added standard HTTP CONNECT / SOCKS proxy support in **1.5.1**, requiring `modal[api-proxy-support]`. Our pinned **1.6.0** already has this functionality and the dependency is installed. Reinstalling, blindly upgrading, or increasing RPC deadlines does not restore missing status frames.

Modal's public issue #4113 describes a different cloud coding environment with suspected gRPC restrictions; it is open and has no maintainer resolution. Issue #4124 requests a fetch/Connect/gRPC-Web transport for edge runtimes; it is also open with no maintainer comments at retrieval. These are corroborating user reports, not proof of this environment's cause or an official commitment to a fallback.

No documented REST/Connect/gRPC-Web replacement for the required sandbox control plane was found in the consulted official SDK source, changelog and sandbox documentation. HTTP services/endpoints inside an existing Sandbox do not solve creation, termination, expiry or reconciliation access. This is a bounded research conclusion, not a claim that no private or future API can exist.

## Recommended resolution

1. **Preferred: fix the managed HTTP path.** Ask the cloud platform owner to trace the read-only reproduction and preserve the gRPC completion status and trailing metadata through every intermediary. Keep TLS verification, destination controls and credential injection intact. The platform may need end-to-end HTTP/2 handling or correct trailer translation if an upstream hop uses HTTP/1.1. The exact proxy implementation/configuration is unknown; do not apply an invented Envoy/nginx setting.
2. **If that path cannot support gRPC: use a supported trusted controller host.** Run the existing controller on a host whose approved Modal network path passes ClientHello with grpc-status 0, with securely provisioned credentials and verified durable same-host storage. Keep learner workers disposable and network-blocked. This requires migration/deployment validation and any incremental infrastructure budget, not just a network toggle.
3. **A managed gRPC-Web/Connect gateway is conditional.** It would need platform/Modal support, proper status conversion, authentication and any required streaming behavior. It is not an available local fix today. Merely changing Content-Type failed.

The configuration tool exposed in this session has no TCP-grant or gRPC-trailer setting. Network policy is already unrestricted. The policy snapshot's TCP grant lists are empty. Editing that snapshot, using an unapproved CONNECT route, disabling verification, disabling inherited proxy routing, or accepting HTTP 200 as RPC success is not a resolution. A provider-issued raw TCP exception would also need a supported credential delivery mechanism because current secrets are proxy-bound.

## Reproduction and acceptance

From `/workspace/recursive-run`:

```bash
UV_CACHE_DIR=/workspace/.cache/uv uv run --extra cloud python scripts/diagnose_modal_transport.py --output .runtime/modal-transport-research.json
UV_CACHE_DIR=/workspace/.cache/uv uv run --extra cloud python scripts/diagnose_modal_transport.py --host api.modal2.com --output .runtime/modal-transport-secondary.json
```

The diagnostic uses only ClientHello and a nonexistent method, with bounded deadlines and no automatic retries. Exit 2 means the transport did not pass; it is the expected current result, not a passing readiness check. The receipt contains allowlisted protocol metadata only.

Acceptance requires ClientHello to complete with a genuine grpc-status 0 (in proper trailing headers or a valid trailers-only completion), the nonexistent method to return genuine status 12, and approved routing/TLS/credential injection to remain intact. Then verify a supported read-only authenticated account operation. Only afterward can runner creation/absolute expiry/dispatch/reconciliation, durable storage, pricing and intake gates be closed before the separately authorized $25 smoke run. A transport fix alone does not complete those gates.

## Support request — ready to copy

> The managed cloud environment reports current enforced unrestricted HTTP policy and ready Modal token bindings. Modal 1.6.0 with api-proxy-support uses the inherited HTTPS proxy and a verified system CA SSLContext. On both api.modal.com and api.modal2.com, POST /modal.client.ModalClient/ClientHello receives HTTP/2 200/application-grpc and a valid protobuf response, followed by DATA END_STREAM with no trailing HEADERS/grpc-status. The SDK waits for trailers and times out. A nonexistent RPC returns grpc-status 12 in its initial headers successfully. Please trace whether upstream trailing metadata is lost and preserve native gRPC completion status through the approved credential-injecting path. We can supply the allowlisted event receipt from scripts/diagnose_modal_transport.py. No paid workers are involved.

No support message has been sent; sending it externally requires explicit user instruction.

## Sources

Retrieved 2026-10-02. Official protocol and source documentation outrank issue reporters' hypotheses.

- [gRPC HTTP/2 protocol](https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md): status is required even for OK; normal completion ends on trailing HEADERS.
- [gRPC-Web protocol](https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-WEB.md): status is encoded in the response body for Web transport.
- [Modal Python changelog, 1.5.1](https://modal.com/docs/sdk/py/changelog): proxy support and optional dependencies.
- [Modal proxy implementation](https://github.com/modal-labs/modal-client/blob/main/py/modal/_utils/proxy_support.py): supported CONNECT/SOCKS transport.
- [Modal maintainer response, #4114](https://github.com/modal-labs/modal-client/issues/4114): confirms proxy support from 1.5.1.
- [Related cloud environment report, #4113](https://github.com/modal-labs/modal-client/issues/4113): user report, unresolved at retrieval.
- [Alternate transport request, #4124](https://github.com/modal-labs/modal-client/issues/4124): user report/request, unresolved at retrieval.
- [Modal Sandboxes guide](https://modal.com/docs/guide/sandboxes): SDK-managed sandbox lifecycle.
- [Envoy gRPC HTTP/1.1 bridge](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/grpc_http1_bridge_filter): an example of explicit protocol/status translation, not evidence that this platform uses Envoy.

Fetched source pages are retained under `/workspace/scratch/modal-research/`; safe live event evidence is under ignored `.runtime/`.
