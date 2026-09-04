import { useState } from "react";
import { envBlock } from "../format";
import type { ProxySnapshot } from "../types";

type Props = { proxy: ProxySnapshot };

export function InfoPage({ proxy }: Props) {
  const [copied, setCopied] = useState(false);
  const listen = proxy.listen || "http://127.0.0.1:9090";
  const ca = proxy.ca || "$HOME/.mitmproxy/mitmproxy-ca-cert.pem";
  const block = envBlock(proxy.listen, proxy.ca, proxy.proxy);

  async function copyEnv() {
    try {
      await navigator.clipboard.writeText(block);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch (err) {
      console.warn(err);
    }
  }

  return (
    <div id="page-info" className="page">
      <div className="info-body">
        <div className="info-hero">
          <h1>How to use</h1>
          <p>One loopback listener. Pin each client once, then toggle Proxy.</p>
        </div>
        <ol className="info-modes">
          <li>
            <strong>Off</strong>
            <p>Tunnel only. No decrypt, no CA. Cost still tracks from journals.</p>
          </li>
          <li>
            <strong>Proxy</strong>
            <p>MITM Anthropic/Bedrock. Capture completed rounds.</p>
          </li>
          <li>
            <strong>Intercept</strong>
            <p>Hold Bedrock invokes. Edit, then Forward or Drop. Off until Proxy is on.</p>
          </li>
        </ol>
        <div className="card">
          <h2>Pin a client</h2>
          <div id="listener-info">
            <div className="row">
              <span className="label">URL</span>
              <span title={listen}>{listen}</span>
            </div>
            <div className="row">
              <span className="label">CA</span>
              <span title={ca}>{ca}</span>
            </div>
          </div>
          <p className="lead">
            {proxy.proxy
              ? "Proxy is on. A new process needs the CA too; restart claude after these exports."
              : "Every new process needs these. With Proxy off, HTTPS is tunneled (no CA)."}
          </p>
          <div className="env-block">
            <button
              type="button"
              id="copy-env"
              className={copied ? "icon-btn copied" : "icon-btn"}
              title={copied ? "Copied" : "Copy exports"}
              aria-label={copied ? "Copied" : "Copy exports"}
              onClick={() => void copyEnv()}
            >
              {copied ? (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : (
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" strokeWidth="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" stroke="currentColor" strokeWidth="2" />
                </svg>
              )}
            </button>
            <pre className="code">{block}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}
