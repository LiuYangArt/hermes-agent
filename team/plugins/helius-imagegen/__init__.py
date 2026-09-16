import importlib.util
import json
import subprocess
import uuid
from pathlib import Path

SCRIPT = Path('/opt/data/skills/helius-imagegen/scripts/helius_imagegen.py')
ROOT = Path('/opt/data/imagegen/helius')

def handle(params, **kwargs):
    try:
        prompt = params.get('prompt', '')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000:
            raise ValueError('Prompt must contain 1..24000 characters')
        mode = params.get('mode', 'generate')
        model = params.get('model', 'gpt-image-2.5-flare')
        quality = params.get('quality', 'auto')
        background = params.get('background', 'auto')
        size = params.get('size', 'auto')
        if mode not in ('generate', 'edit') or model not in ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst'):
            raise ValueError('Unsupported mode or model')
        if quality not in ('auto','low','medium','high','xhigh','max') or background not in ('auto','opaque','transparent'):
            raise ValueError('Unsupported quality or background')
        refs = params.get('images', [])
        if not isinstance(refs, list) or len(refs) > 8:
            raise ValueError('At most 8 reference images')
        from gateway.platforms.base import validate_media_delivery_path
        images = []
        for ref in refs:
            safe = validate_media_delivery_path(ref)
            if not safe or Path(safe).suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
                raise ValueError('Reference must be an allowed local image')
            images.append(safe)
        if (mode == 'edit' and not images) or (mode == 'generate' and images):
            raise ValueError('Edit requires images; generate must not include images')
        ROOT.mkdir(parents=True, exist_ok=True)
        stem = uuid.uuid4().hex
        prompt_file = ROOT / (stem + '.txt')
        out = ROOT / (stem + '.png')
        prompt_file.write_text(prompt, encoding='utf-8')
        command = ['/opt/hermes/.venv/bin/python', str(SCRIPT), '--prompt-file', str(prompt_file), '--out', str(out), '--mode', mode, '--model', model, '--quality', quality, '--size', size, '--background', background]
        for ref in images:
            command.extend(['--image', ref])
        result = subprocess.run(command, capture_output=True, text=True, timeout=330, cwd='/opt/hermes')
        report_path = out.with_suffix('.json')
        if report_path.exists():
            report = json.loads(report_path.read_text())
            return json.dumps({'success': result.returncode == 0, 'report': str(report_path), 'images': report['images'], 'warnings': report['warnings'], 'media': ['MEDIA:' + x['path'] for x in report['images']], 'model_identity': 'See reported_image_model; missing means unknown'}, ensure_ascii=False)
        return json.dumps({'success':False,'error':(result.stderr or result.stdout)[-1800:], 'retry':False})
    except subprocess.TimeoutExpired:
        return json.dumps({'success':False,'error':'Timed out; do not retry automatically because the provider may have billed the request','retry':False})
    except Exception as exc:
        return json.dumps({'success':False,'error':str(exc)[:500],'retry':False})

def register(ctx):
    schema = {'name':'helius_generate_image','description':'Generate or edit an image using Helius GPT Image 2.5. Pass prompt text directly; no shell or prompt-file preparation needed. Returns local images, reports and MEDIA tags. Never automatically retry failures.','parameters':{'type':'object','properties':{
        'prompt':{'type':'string'}, 'mode':{'type':'string','enum':['generate','edit']},
        'images':{'type':'array','items':{'type':'string'},'description':'User-selected local reference image paths; edit only'},
        'size':{'type':'string','description':'auto or WIDTHxHEIGHT'},
        'quality':{'type':'string','enum':['auto','low','medium','high','xhigh','max']},
        'background':{'type':'string','enum':['auto','opaque','transparent']},
        'model':{'type':'string','enum':['gpt-image-2.5-flare','gpt-image-2.5-sunburst']}},'required':['prompt'],'additionalProperties':False}}
    ctx.register_tool(name='helius_generate_image',toolset='helius_imagegen',schema=schema,handler=handle)
