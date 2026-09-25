import './globals.css';

export const metadata = {
  title: 'AI SRE RCA',
  description: 'Evidence-first autonomous incident investigation'
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
