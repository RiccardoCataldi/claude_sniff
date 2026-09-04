# claude-sniff

Loopback MITM for Claude Code on Amazon Bedrock: one process, a listener and a monitoring UI.

## Language

**Catalog**:
The store of sessions, traffic records, and usage, from proxy captures and Claude diaries.
_Avoid_: logs, history, database

**Control**:
Whether the proxy is decrypting, whether invokes are held, the held head and queue, and the live sessions sniffed since proxy was last turned on.
_Avoid_: addon, CaptureAddon, MITM state

**Monitor**:
The operator's live picture of catalog and control.
_Avoid_: live catalog view, dashboard, App state, store
