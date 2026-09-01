import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Dibs, a Reservation Sniper",
  description:
    "Dibs watches Kitchener–Waterloo restaurants and recreation and grabs the reservation the moment a table opens.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
