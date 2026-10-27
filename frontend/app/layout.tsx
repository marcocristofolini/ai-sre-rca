import './globals.css';
import './tracecroft.css';

export const metadata = {
  title: 'Tracecroft AI | Evidence-first incident investigation',
  description: 'Tracecroft AI investigates incidents across Kubernetes, telemetry and service context. Verify root causes with evidence and keep remediation behind explicit controls.'
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
