import './globals.css';

export const metadata = {
  title: 'AI SRE RCA | Evidence-first incident investigation',
  description: 'Investigate incidents across Kubernetes, telemetry and service context. Verify root causes with evidence and keep remediation behind explicit controls.'
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
