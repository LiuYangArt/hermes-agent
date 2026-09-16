import fs from 'node:fs';
import {ensureTaskRuntimeInstanceConfigs} from '@iluolyx/aamp-feishu-bridge/dist/task-runtime.js';
const app=JSON.parse(fs.readFileSync('runtime/registered-app.json'));
const acp=JSON.parse(fs.readFileSync('runtime/hermes-credentials.json'));
const result=await ensureTaskRuntimeInstanceConfigs({agent:{type:'hermes',display_name:'Hermes Team Tasks',target_agent_email:acp.email,execution_location:'local'},bot:{app_id:app.client_id,app_secret:app.client_secret,auth_mode:'app-secret',display_name:'Hermes Team Tasks'}},{configDir:process.cwd()+'/runtime/lark',aampHost:'https://meshmail.ai',domain:'https://open.larksuite.com',appSecret:app.client_secret});
fs.writeFileSync('runtime/paths.json',JSON.stringify({taskDir:result.taskDir,imDir:result.imDir}));
fs.writeFileSync('runtime/sender-policies.json',JSON.stringify({version:1,policies:[{sender:result.taskConfig.mailbox.email,dispatchContextRules:{source:['feishu-task']}}]}),{mode:0o600});
console.log(JSON.stringify({taskDir:result.taskDir,mailbox:result.taskConfig.mailbox.email}));
