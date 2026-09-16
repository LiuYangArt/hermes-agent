import getpass,json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
print('Hermes Team Tasks - Lark app credentials')
app_id=input('App ID: ').strip()
secret=getpass.getpass('App Secret (hidden): ').strip()
try:
 if not app_id.startswith('cli_') or not secret: raise ValueError('Missing app credentials')
 req=urllib.request.Request('https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal',data=json.dumps({'app_id':app_id,'app_secret':secret}).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=25) as r: data=json.load(r)
 if data.get('code')!=0 or not data.get('tenant_access_token'):raise ValueError('Lark credential validation failed, code '+str(data.get('code')))
 dest=root/'runtime/registered-app.json'
 dest.write_text(json.dumps({'client_id':app_id,'client_secret':secret,'user_info':{'tenant_brand':'lark'}}))
 dest.chmod(0o600)
 print('Validated with Lark and saved. Return to Codex and say: saved.')
except Exception as exc:
 print('Not saved: '+type(exc).__name__)
input('Press Enter to close...')
