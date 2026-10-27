# Tracecroft AI | Public website

A static bilingual project presentation, independent from the Next.js investigation application. The dark visual identity and circuit-shaped T mark are original.

## Included
- Responsive HTML/CSS and EN/PT toggle, self-hosted and tracker-free.
- Actual Chromium screenshots, with enlarged-image dialog.
- Independent multi-root RCA, scoped orchestration, Copilot, source integrations, verified benchmark results and honest limitations.
- Exact preserved Archify 3.1.0 interactive architecture in a local iframe.
- SEO metadata, sitemap, favicon, accessibility labels and Apache headers.

## Validate and preview
From the repository root:

    node website/check.cjs
    cd website && python3 -m http.server 8090 --bind 127.0.0.1

Use a real browser to check mobile/desktop layouts, EN/PT, image lightbox and the Archify iframe.

## Deploy to cPanel
The intended subdomain is tracecroft-ai.marcocristofolini.dev. You must first create it through WHM/cPanel, verify the actual document root, confirm DNS and provision an HTTPS certificate. The main portfolio at /home/marcofc/public_html must not be replaced.

Only after verifying the subdomain root, use the authorized WSL computer:

    TRACE_DOCROOT=/home/marcofc/VERIFIED_SUBDOMAIN_DOCROOT \
      TRACE_SSH_TARGET=YOUR_AUTHORIZED_SSH_USER@HOST \
      bash website/deploy-cpanel.sh

Defaults are SSH port 2006 and ~/.ssh/id_ed25519; both are configurable. The script requires an existing target directory and copies only files without --delete. Validate the public HTTPS site afterward. A prepared branch does not mean deployment succeeded.

## Integrity
The screenshot gallery comprises real browser captures of the controlled local demo and one frozen Kind result. The validation numbers describe local cases, not production accuracy, certification or an SLA. The site borrows general dark developer-site conventions without copying Sherlocks AI source, graphics, branding or claims.
