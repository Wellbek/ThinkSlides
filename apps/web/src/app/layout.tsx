import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SlideAgent — AI Presentation Generator",
  description: "Generate fully branded, template-faithful PowerPoint presentations through AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="h-screen overflow-hidden">
        {children}
      </body>
    </html>
  );
}