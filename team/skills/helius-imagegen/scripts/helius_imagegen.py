"""Helius Responses image generation; credentials never enter command arguments."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import httpx
import sys
import time
from urllib.parse import urlparse
from PIL import Image


def size_value(value):
    if value == 'auto':
        return value
    if not re.fullmatch(r'[1-9][0-9]*x[1-9][0-9]*', value):
        raise argparse.ArgumentTypeError('size must be auto or WIDTHxHEIGHT')
    w, h = map(int, value.split('x'))
    if max(w, h) > 3840 or w % 16 or h % 16 or max(w, h) > 3 * min(w, h) or not 655360 <= w*h <= 8294400:
        raise argparse.ArgumentTypeError('size violates official 2.5 limits: edges <=3840, multiples of 16, ratio <=3:1, pixels 655360..8294400')
    return value

def image_info(data):
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        alpha = im.convert('RGBA').getchannel('A').getextrema()
        return {'size': f'{im.width}x{im.height}', 'format': im.format.lower(), 'has_transparency': alpha[0] < 255}

def build_request(a):
    prompt = Path(a.prompt_file).read_text(encoding='utf-8-sig').strip()
    if not prompt:
        raise ValueError('Prompt file is empty')
    mode = a.mode if a.mode != 'auto' else ('edit' if a.image else 'generate')
    if mode == 'edit' and not a.image:
        raise ValueError('Image-to-image requires --image')
    if mode == 'generate' and a.image:
        raise ValueError('Explicit text-to-image must not include --image')
    if a.background == 'transparent' and a.format == 'jpeg':
        raise ValueError('Transparent output requires png or webp')
    if a.compression is not None and (a.format == 'png' or not 0 <= a.compression <= 100):
        raise ValueError('Compression is 0..100 and only supports jpeg/webp')
    tool = dict(type='image_generation', model=a.model, action=mode, size=a.size, quality=a.quality, background=a.background, output_format=a.format)
    if a.compression is not None:
        tool['output_compression'] = a.compression
    content = [dict(type='input_text', text=prompt)]
    for n, filename in enumerate(a.image, 1):
        data = Path(filename).read_bytes()
        info = image_info(data)
        if info['format'] not in ('png', 'jpeg', 'webp'):
            raise ValueError('Input must be PNG, JPEG or WebP')
        content.append(dict(type='input_text', text=f'Image {n}'))
        content.append(dict(type='input_image', image_url=f"data:image/{info['format']};base64," + base64.b64encode(data).decode()))
    return dict(model=a.main_model, input=[dict(role='user', content=content)], tools=[tool], tool_choice=dict(type='image_generation'))

def provider():
    # Reuse the active Hermes credentials without creating a second secret store.
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.auxiliary_client import _apply_user_default_headers
    runtime = resolve_runtime_provider()
    base = str(runtime.get('base_url') or '').rstrip('/')
    url = urlparse(base)
    if url.scheme != 'https' or not (url.hostname or '').endswith('.projecthelius.com') or url.username or url.password:
        raise ValueError('Current Hermes provider is not an HTTPS Helius endpoint; refusing to send images')
    token = runtime.get('api_key')
    if not token:
        raise ValueError('No credential in current Hermes provider; configure it locally')
    headers = _apply_user_default_headers(None) or {}
    headers['Authorization'] = 'Bearer ' + token
    headers.setdefault('User-Agent', 'Hermes-Agent')
    return base + '/responses', token, headers

def run(a):
    request = build_request(a)
    out = Path(a.out).resolve()
    expected_suffix = {'png':'.png','jpeg':'.jpg','webp':'.webp'}[a.format]
    if out.suffix.lower() not in ([expected_suffix, '.jpeg'] if a.format == 'jpeg' else [expected_suffix]):
        raise ValueError('Output filename extension does not match requested format')
    report_path = out.with_suffix('.json')
    if out.exists() or report_path.exists():
        raise ValueError('Output or report already exists; choose a new filename')
    if a.dry_run:
        print(json.dumps({'dry_run':True,'main_model':a.main_model,'tool':request['tools'][0],'image_count':len(a.image),'out':str(out)},ensure_ascii=False))
        return 0
    url, token, headers = provider()
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=a.timeout, follow_redirects=False) as client:
            result = client.post(url, headers=headers, json=request)
        if not 200 <= result.status_code < 300:
            detail = result.text.replace(token, '[REDACTED]')[:1200]
            raise RuntimeError(f'Request failed HTTP {result.status_code}; not automatically retried. ' + detail)
        response = result.json()
    except httpx.HTTPError as exc:
        raise RuntimeError('Transport failed; not automatically retried: ' + type(exc).__name__) from None
    if response.get('error'):
        raise RuntimeError('Provider returned an error: ' + json.dumps(response['error']).replace(token,'[REDACTED]')[:1200])
    calls = [x for x in response.get('output',[]) if x.get('type') == 'image_generation_call' and x.get('result')]
    if not calls:
        raise RuntimeError('No image data returned; response id=' + str(response.get('id')))
    warnings = []
    records = []
    for i, call in enumerate(calls):
        data = base64.b64decode(call['result'],validate=True)
        info = image_info(data)
        suffix = {'png':'.png','jpeg':'.jpg','webp':'.webp'}.get(info['format'])
        if not suffix:
            raise ValueError('Unexpected returned image format')
        target = out.with_suffix(suffix) if i == 0 else out.with_name(f'{out.stem}-{i+1}').with_suffix(suffix)
        with target.open('xb') as f:
            f.write(data)
        if call.get('model') and call['model'] != a.model:
            warnings.append(f"Requested model {a.model}, provider reported {call['model']}")
        if a.size != 'auto' and info['size'] != a.size:
            warnings.append(f"Requested size {a.size}, returned {info['size']}; not resized")
        if info['format'] != a.format:
            warnings.append('Returned file format differs from request')
        if a.background == 'transparent' and not info['has_transparency']:
            warnings.append('Requested transparency is absent')
        if call.get('action') != request['tools'][0]['action']:
            warnings.append('Returned action absent or differs from requested action')
        if call.get('status') != 'completed':
            warnings.append('Image call did not report completed')
        records.append(dict(path=str(target),bytes=len(data),**info,call_id=call.get('id'),action=call.get('action'),reported_image_model=call.get('model'),model_identity_status=('reported_by_provider' if call.get('model') else 'unknown_not_reported_by_provider'),revised_prompt=call.get('revised_prompt')))
    report = dict(response_id=response.get('id'),requested_image_model=a.model,requested_options=request['tools'][0],prompt=Path(a.prompt_file).read_text(encoding='utf-8-sig'),input_images=[str(Path(x).resolve()) for x in a.image],elapsed_seconds=round(time.monotonic()-started,2),images=records,warnings=warnings,usage=response.get('usage'))
    with report_path.open('x',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(report=str(report_path),images=records,warnings=warnings),ensure_ascii=False))
    return 2 if warnings else 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prompt-file',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--mode',choices=['auto','generate','edit'],default='auto')
    p.add_argument('--image',action='append',default=[])
    p.add_argument('--model',choices=['gpt-image-2.5-flare','gpt-image-2.5-sunburst'],default='gpt-image-2.5-flare')
    p.add_argument('--main-model',default='cx/gpt-6-astra')
    p.add_argument('--size',type=size_value,default='auto')
    p.add_argument('--quality',choices=['auto','low','medium','high','xhigh','max'],default='auto')
    p.add_argument('--background',choices=['auto','opaque','transparent'],default='auto')
    p.add_argument('--format',choices=['png','jpeg','webp'],default='png')
    p.add_argument('--compression',type=int)
    p.add_argument('--timeout',type=int,default=300)
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    try:
        return run(a)
    except (ValueError,OSError,RuntimeError) as e:
        print(str(e),file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
