import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ThinkSlides — AI Presentation Generator",
  description: "Generate fully branded, template-faithful PowerPoint presentations through AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="h-screen overflow-hidden bg-charcoal text-paper font-sans antialiased">
        {children}
      </body>
    </html>
  );
}
