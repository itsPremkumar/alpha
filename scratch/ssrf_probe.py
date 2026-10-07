from alpha.community.url_safety import validate_public_http_url, validate_model_endpoint_url

def chk(name, url, **kwargs):
    try:
        r = validate_model_endpoint_url(url, **kwargs)
        print(f'{name:45s} -> {str(r)[:80]}')
    except Exception as e:
        print(f'{name:45s} -> RAISED {type(e).__name__}')

print('=== validate_public_http_url ===')
for name, url in [
    ('https://example.com', 'https://example.com'),
    ('http 127.0.0.1', 'http://127.0.0.1/index'),
    ('http 10.0.0.1', 'http://10.0.0.1:8000/x'),
    ('localhost', 'localhost:8080'),
    ('metadata.google.internal', 'http://metadata.google.internal/x'),
]:
    r = validate_public_http_url(url)
    print(f'{name:45s} -> {str(r)[:80]}')

print('=== model endpoint default (no opt-in) ===')
for name, url in [
    ('public openai', 'https://api.openai.com/v1'),
    ('local ollama', 'http://127.0.0.1:11434/v1'),
    ('10.0.0.1', 'http://10.0.0.1:8000/v1'),
    ('metadata.internal', 'http://metadata.internal/v1'),
    ('example public', 'https://example.com/v1'),
]:
    chk(name, url)

print('=== model endpoint local tier (allow_loopback=True) ===')
for name, url in [
    ('local ollama', 'http://127.0.0.1:11434/v1'),
    ('10.0.0.1', 'http://10.0.0.1:8000/v1'),
    ('metadata.google.internal', 'http://metadata.google.internal/v1'),
]:
    chk(name, url, allow_loopback=True)

print('=== embedded credentials ===')
for name, url in [
    ('user:pass', 'https://user:pass@example.com/v1'),
    ('x:y 169.254', 'http://x:y@169.254.169.254/latest'),
]:
    chk(name, url)
