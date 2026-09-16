from pathlib import Path
p=Path(__file__).parent/'node_modules/@luckyterry/aamp-acp-bridge/dist/acpx-client.js'
s=p.read_text();old="'--approve-all'";new="'--deny-all', '--non-interactive-permissions', 'deny', '--no-terminal'"
if s.count(old)==2:p.write_text(s.replace(old,new))
else:assert s.count(new)==2,'Unexpected upstream permission argument layout'
print('Dedicated bridge approval protections verified')
