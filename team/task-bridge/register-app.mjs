import * as lark from '@larksuiteoapi/node-sdk';
import fs from 'node:fs';
const manifest=JSON.parse(fs.readFileSync('runtime/scope-manifest.json','utf8'));
const result=await lark.registerApp({
 source:'aamp-feishu-task-agent',createOnly:true,
 appPreset:{name:'Hermes Team Tasks',desc:'Assign Lark Tasks to the existing Docker Hermes Team and receive execution results.'},
 addons:{preset:false,scopes:manifest.app,events:{items:{tenant:['task.task.update_user_access_v2','im.message.receive_v1'],user:['task.task.update_user_access_v2']}},callbacks:{items:['card.action.trigger']}},
 onQRCodeReady(info){fs.writeFileSync('runtime/registration-url.json',JSON.stringify(info),{mode:0o600});console.log(JSON.stringify(info));},
 onStatusChange(info){if(info.status!=='polling')console.log(JSON.stringify(info));}
});
fs.writeFileSync('runtime/registered-app.json',JSON.stringify(result),{mode:0o600});
console.log(JSON.stringify({registered:true,appId:result.client_id,brand:result.user_info?.tenant_brand}));
