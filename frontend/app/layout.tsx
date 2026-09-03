import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AuthProvider } from "./_components/AuthProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dibs, a Reservation Sniper",
  description:
    "Dibs watches Kitchener–Waterloo restaurants and recreation and grabs the reservation the moment a table opens.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/*
          Night Scope's three faces: Newsreader (serif display), Hanken Grotesk
          (body), JetBrains Mono (HUD telemetry). Loaded at runtime via a plain
          <link> rather than next/font so the build never needs network access.
        */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300;0,6..72,400;1,6..72,300;1,6..72,400&family=Hanken+Grotesk:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap"
        />
      </head>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
