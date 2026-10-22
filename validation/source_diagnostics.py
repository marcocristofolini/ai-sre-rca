"""Bounded, credential-redacted source failures for local validation artifacts."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE = r'(?:password|passwd|token|secret|api[_-]?key|authorization|cookie)'


def redact(text):
    text = re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', str(text))
    text = re.sub(r'(?i)(https?://|postgres(?:ql)?(?:\+asyncpg)?://)([^/@\s]+)@', r'\1[REDACTED]@', text)
    return re.sub(r'(?i)(["\']?'+SENSITIVE+r'["\']?\s*[:=]\s*["\']?)[^\s,;"\'}&]+', r'\1[REDACTED]', text)


def safe_url(url):
    parts = urlsplit(str(url))
    # Never retain userinfo or sensitive query parameters. No request headers.
    netloc = parts.netloc.rsplit('@', 1)[-1]
    query = urlencode([(k, '[REDACTED]' if re.search(SENSITIVE, k, re.I) else v)
                       for k, v in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, netloc, parts.path, query, ''))


def source_failure(exc):
    response = getattr(exc, 'response', None)
    try:
        request = getattr(exc, 'request', None)
    except RuntimeError:  # httpx permits transport errors without an attached request.
        request = None
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append({'type': type(current).__name__, 'message': redact(str(current))[:2000]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return {'type': type(exc).__name__, 'status': getattr(response, 'status_code', None),
            'request': {'method': request.method, 'url': safe_url(request.url)} if request else None,
            'response_body': redact(response.text)[:8192] if response is not None else None,
            'exception_chain': chain}


def capture_source_logs(context, directory):
    """Use only the explicitly supplied disposable validation context."""
    from validation import run as harness
    for source in ('tempo',):
        logs = harness.run(['kubectl', '--context', context, 'logs', '-n',
                            harness.NAMESPACE, 'deployment/'+source, '--tail=2000'], check=False)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (source+'.log')).write_text(redact(logs.stdout + logs.stderr))
