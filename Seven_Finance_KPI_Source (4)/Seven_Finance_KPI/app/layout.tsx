import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Seven Group | KPI Head of Finance",
  description: "Ежедневные сдачи, календарь выплат, замечания и прозрачный расчёт KPI Head of Finance Seven Group.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body className="antialiased">{children}</body>
    </html>
  );
}
