/**
 * Security headers (P2-21).
 *
 * CSP notes — this is the pragmatic policy for a Next 14 app-router app:
 *  - style-src/script-src need 'unsafe-inline' (Next inlines its bootstrap
 *    markup and React renders styles inline); the rest is locked down.
 *  - connect-src adds the API origin derived from NEXT_PUBLIC_API_BASE_URL
 *    at config-load time; without it, the fetch() health check and every
 *    API call would be blocked by our own CSP.
 *  - dev-only sources keep HMR (localhost websocket) working under `next dev`.
 *
 * HSTS is deliberately NOT enabled here: sending it over plain HTTP is
 * harmless but committing to it before every deployment target serves HTTPS
 * (and terminates TLS correctly) risks locking operators out. Enable it at
 * the HTTPS edge/CDN, or uncomment for production deployments:
 *
 *   { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
 */

const FALLBACK_API_BASE = "http://localhost:8000/api/v1";

function apiOrigin(base) {
  try {
    const url = new URL(base);
    // A relative base ("/api/v1") means same-origin → 'self' already covers it.
    return url.origin;
  } catch {
    return null;
  }
}

const isDev = process.env.NODE_ENV !== "production";
const origin = apiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL ?? FALLBACK_API_BASE);

const connectSrc = [
  "'self'",
  origin,
  ...(isDev ? ["ws://localhost:3000", "ws://127.0.0.1:3000"] : []),
]
  .filter(Boolean)
  .join(" ");

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  `connect-src ${connectSrc}`,
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "frame-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
  // Production only — see the HSTS note at the top of this file:
  // { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
