import type { Metadata } from "next";
import localFont from "next/font/local";
import { ApiHealthBanner } from "./components/api-health-banner";
import { DemoModeBanner } from "./components/auth-nudge";
import { Nav } from "./components/nav";
import { Providers } from "./providers";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

/** P2-20: shared title template so every route that exports `metadata`
 *  renders as "<page> · AdmitGraph" instead of a bare "AdmitGraph". */
export const metadata: Metadata = {
  title: {
    default: "AdmitGraph — study abroad with a plan",
    template: "%s · AdmitGraph",
  },
  description: "Study abroad with a plan, not a pile of tabs.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>
          <Nav />
          {/* Cross-cutting notices: unreachable API base (P2-21) + guest
              demo-profile disclosure for anonymous visitors (P2-18/P0-3). */}
          <ApiHealthBanner />
          <DemoModeBanner />
          {children}
        </Providers>
      </body>
    </html>
  );
}
